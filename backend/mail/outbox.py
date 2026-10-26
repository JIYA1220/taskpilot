"""Outbox: queue emails, send them with retries, and never send one twice or drop one silently.

Why an outbox? A status change (task done, deadline missed) and its email must not drift
apart. The email is written to the database in the same transaction as the change, and a
separate step sends it. If sending fails, the row stays and is retried later with
exponential backoff; if it fails for good, the owner is told and can retry by hand."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlmodel import Session, select

from backend.mail import render
from backend.mail.sender import EmailSender, EmailSendError, OutgoingEmail
from backend.models import AuditLog, EmailDraft, EmailEvent, Notification, Task, User

log = logging.getLogger(__name__)

CLAIM_TIMEOUT = timedelta(minutes=10)
BATCH = 50
KIND_LABEL = {"completed": "completion", "missed": "missed-deadline", "draft": "task"}


def queue_event(
    session: Session, task: Task, kind: str, recipient: str, content: tuple[str, str, str], now: datetime
) -> EmailEvent | None:
    """Add an outbox row unless one already exists for this task and kind (returns None then).
    The unique constraint is the real guard; the lookup avoids tripping it in normal use."""
    exists = session.exec(select(EmailEvent.id).where(EmailEvent.task_id == task.id, EmailEvent.kind == kind)).first()
    if exists is not None:
        return None
    subject, text, html = content
    event = EmailEvent(
        task_id=task.id,
        kind=kind,
        recipient=recipient.strip().lower(),
        subject=subject,
        body_text=text,
        body_html=html,
        next_attempt_at=now,
        created_at=now,
    )
    session.add(event)
    session.flush()
    return event


def queue_reviewer_event(session: Session, task: Task, owner: User, kind: str, now: datetime, tz: ZoneInfo) -> EmailEvent | None:
    """Email the task's reviewer about a completion or a missed deadline, and tell the
    reviewer in-app if they are a TaskPilot user. Once per task and kind."""
    if not task.reviewer_email or kind not in ("completed", "missed"):
        return None
    event = queue_event(session, task, kind, task.reviewer_email, render.reviewer_email(task, owner, kind, now, tz), now)
    if event is None:
        return None
    reviewer = session.exec(select(User).where(User.email == task.reviewer_email.lower())).first()
    if reviewer is not None:
        verb = "completed" if kind == "completed" else "missed the deadline for"
        session.add(
            Notification(
                user_id=reviewer.id,
                kind=f"review_{kind}",
                message=f'{owner.name} {verb} "{task.title}", which you are reviewing.',
                payload={"task_id": task.id},
                created_at=now,
            )
        )
    return event


def _claim(session: Session, event_id: int, now: datetime) -> bool:
    """Atomically move pending -> sending. Only one caller can win, so the request thread
    and the scheduler thread can never both send the same row."""
    result = session.execute(
        update(EmailEvent).where(EmailEvent.id == event_id, EmailEvent.state == "pending").values(state="sending", claimed_at=now)
    )
    session.commit()
    return result.rowcount == 1


def _audit(session: Session, event: EmailEvent, ok: bool, detail: str) -> None:
    # Who and what kind, never the message body.
    session.add(AuditLog(kind="email", prompt_name=event.kind, parsed_ok=ok, attempt=event.attempts, detail=f"to {event.recipient}: {detail}"[:300]))


def _after_sent(session: Session, event: EmailEvent, task: Task, owner: User, now: datetime, tz: ZoneInfo) -> EmailEvent | None:
    """Follow-up once an email has gone out. Returns a newly queued event, if any."""
    if event.kind != "draft":
        return None
    draft = session.exec(
        select(EmailDraft).where(EmailDraft.task_id == task.id, EmailDraft.state == "approved").order_by(EmailDraft.id.desc())
    ).first()
    if draft:
        draft.state = "sent"
        session.add(draft)
    # Sending the email is the task. It is done now, and the reviewer hears about it.
    if task.status != "done":
        task.status = "done"
        task.completed_at = now
        session.add(task)
        return queue_reviewer_event(session, task, owner, "completed", now, tz)
    return None


def _fail(session: Session, event: EmailEvent, task: Task, error: EmailSendError, now: datetime, max_attempts: int, base: int) -> None:
    event.attempts += 1
    event.last_error = str(error)[:300]
    event.claimed_at = None
    _audit(session, event, False, event.last_error)
    if not error.retryable or event.attempts >= max_attempts:
        event.state = "failed"
        session.add(
            Notification(
                user_id=task.owner_id,
                kind="email_failed",
                message=(
                    f'Could not send the {KIND_LABEL.get(event.kind, event.kind)} email for "{task.title}" '
                    f"to {event.recipient} ({event.last_error}). Open the task to retry."
                ),
                payload={"task_id": task.id, "event_id": event.id},
                created_at=now,
            )
        )
    else:
        event.state = "pending"
        event.next_attempt_at = now + timedelta(seconds=base * 2 ** (event.attempts - 1))
    session.add(event)


def process_outbox(
    session: Session,
    now: datetime,
    sender: EmailSender,
    tz: ZoneInfo,
    *,
    max_attempts: int = 5,
    retry_base_seconds: int = 60,
    only_ids: list[int] | None = None,
) -> list[EmailEvent]:
    """Send everything that is due. Returns the events attempted. Never raises for a send problem."""
    session.execute(  # a crash mid-send leaves 'sending' behind; give such rows back after a while
        update(EmailEvent).where(EmailEvent.state == "sending", EmailEvent.claimed_at < now - CLAIM_TIMEOUT).values(state="pending", claimed_at=None)
    )
    session.commit()
    query = select(EmailEvent.id).where(EmailEvent.state == "pending", EmailEvent.next_attempt_at <= now)
    if only_ids is not None:
        query = query.where(EmailEvent.id.in_(only_ids))
    attempted = []
    pending_ids = list(session.exec(query.order_by(EmailEvent.id).limit(BATCH)).all())
    while pending_ids:
        event_id = pending_ids.pop(0)
        if not _claim(session, event_id, now):
            continue  # someone else has it
        event = session.get(EmailEvent, event_id)
        task = session.get(Task, event.task_id)
        owner = session.get(User, task.owner_id)
        try:
            sender.send(OutgoingEmail(event.recipient, event.subject, event.body_text, event.body_html, owner.name))
        except EmailSendError as exc:
            _fail(session, event, task, exc, now, max_attempts, retry_base_seconds)
        except Exception as exc:  # a bug in a sender must not kill the loop or lose the row
            log.exception("unexpected error while sending email event %s", event_id)
            _fail(session, event, task, EmailSendError(f"{type(exc).__name__}", retryable=True), now, max_attempts, retry_base_seconds)
        else:
            event.state, event.sent_at, event.claimed_at, event.last_error = "sent", now, None, ""
            event.attempts += 1
            session.add(event)
            _audit(session, event, True, "sent")
            follow_up = _after_sent(session, event, task, owner, now, tz)
            if follow_up is not None:  # e.g. the reviewer's "completed" email: send it in this same pass
                session.flush()
                pending_ids.append(follow_up.id)
        session.commit()
        attempted.append(event)
    return attempted


def retry_failed(session: Session, event: EmailEvent, now: datetime) -> bool:
    """Manual retry of an email that ran out of attempts."""
    if event.state != "failed":
        return False
    event.state, event.attempts, event.next_attempt_at, event.last_error = "pending", 0, now, ""
    session.add(event)
    session.commit()
    return True
