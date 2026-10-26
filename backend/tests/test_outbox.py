"""The outbox: once-only delivery, retries with backoff, honest failure, claims, and reviewer events."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from backend.config import Settings
from backend.mail.outbox import _claim, process_outbox, queue_event, queue_reviewer_event, retry_failed
from backend.models import AuditLog, EmailEvent, Notification, Task, User
from backend.reminders import run_tick
from backend.scheduler import tick_once
from backend.tests.fakes import FakeSender
from backend.tests.test_reminders import IST, ist

T0 = ist(2, 10)
RAVI = "ravi.kumar@acme.example.com"
OWNER = 1  # Jiya
MEERA_USER = 3


def mk(session, title="Send the report", reviewer=RAVI, **kw) -> Task:
    task = Task(owner_id=OWNER, title=title, reviewer_email=reviewer, problem_statement="Goal: do it", **kw)
    session.add(task)
    session.commit()
    return task


def queue(session, task, kind="completed", now=T0) -> EmailEvent:
    event = queue_reviewer_event(session, task, session.get(User, task.owner_id), kind, now, IST)
    session.commit()
    return event


def run(session, now, sender, **kw):
    return process_outbox(session, now, sender, IST, max_attempts=kw.pop("max_attempts", 5), retry_base_seconds=60, **kw)


def events(session):
    return session.exec(select(EmailEvent).order_by(EmailEvent.id)).all()


# ---- once per task and kind -------------------------------------------------


def test_an_event_is_queued_once_per_task_and_kind(seeded):
    task = mk(seeded)
    assert queue(seeded, task, "completed") is not None
    assert queue(seeded, task, "completed") is None
    assert queue(seeded, task, "missed") is not None
    assert [e.kind for e in events(seeded)] == ["completed", "missed"]


def test_no_reviewer_or_an_unsupported_kind_queues_nothing(seeded):
    assert queue(seeded, mk(seeded, reviewer=None)) is None
    assert queue(seeded, mk(seeded), "draft") is None
    assert events(seeded) == []


def test_the_database_itself_rejects_a_duplicate(seeded):
    task = mk(seeded)
    queue(seeded, task)
    seeded.add(EmailEvent(task_id=task.id, kind="completed", recipient=RAVI, subject="s", body_text="t", body_html="h"))
    with pytest.raises(IntegrityError):
        seeded.commit()
    seeded.rollback()


# ---- sending ----------------------------------------------------------------


def test_sends_once_marks_sent_and_audits_without_the_body(seeded):
    sender = FakeSender()
    event = queue(seeded, mk(seeded))
    [attempted] = run(seeded, T0, sender)
    assert attempted.id == event.id and len(sender.sent) == 1
    mail = sender.sent[0]
    assert mail.to == RAVI and mail.subject == "[TaskPilot] Completed: Send the report" and mail.from_name == "Jiya Sharma"
    seeded.refresh(event)
    assert (event.state, event.attempts, event.sent_at) == ("sent", 1, T0)
    [audit] = seeded.exec(select(AuditLog).where(AuditLog.kind == "email")).all()
    assert audit.parsed_ok is True and RAVI in audit.detail and "Goal" not in audit.detail
    assert run(seeded, T0 + timedelta(hours=5), sender) == [] and len(sender.sent) == 1


def test_nothing_is_sent_before_it_is_due(seeded):
    sender = FakeSender()
    queue(seeded, mk(seeded), now=T0 + timedelta(minutes=5))
    assert run(seeded, T0, sender) == [] and sender.sent == []
    assert len(run(seeded, T0 + timedelta(minutes=5), sender)) == 1


# ---- retries ----------------------------------------------------------------


def test_failed_sends_back_off_exponentially_then_succeed(seeded):
    sender = FakeSender()
    sender.fail_next(2)
    event = queue(seeded, mk(seeded))
    run(seeded, T0, sender)
    seeded.refresh(event)
    assert (event.state, event.attempts, event.next_attempt_at, event.last_error) == ("pending", 1, T0 + timedelta(seconds=60), "connection refused")
    assert run(seeded, T0 + timedelta(seconds=30), sender) == [], "not due yet"
    run(seeded, T0 + timedelta(seconds=60), sender)
    seeded.refresh(event)
    assert (event.state, event.attempts, event.next_attempt_at) == ("pending", 2, T0 + timedelta(seconds=60 + 120))
    assert run(seeded, T0 + timedelta(seconds=60 + 119), sender) == []
    run(seeded, T0 + timedelta(seconds=60 + 120), sender)
    seeded.refresh(event)
    assert (event.state, event.attempts, event.last_error) == ("sent", 3, "") and len(sender.sent) == 1


def test_it_gives_up_after_the_limit_and_tells_the_owner_exactly_once(seeded):
    sender = FakeSender()
    sender.fail_next(10, message="mailbox unavailable")
    event = queue(seeded, mk(seeded, title="Quarterly report"))
    for offset in (0, 60, 180):  # attempts at +0, +60 and +60+120 with a limit of 3
        run(seeded, T0 + timedelta(seconds=offset), sender, max_attempts=3)
    seeded.refresh(event)
    assert event.state == "failed" and event.attempts == 3
    [note] = seeded.exec(select(Notification).where(Notification.kind == "email_failed")).all()
    assert note.user_id == OWNER and "Quarterly report" in note.message and RAVI in note.message and "mailbox unavailable" in note.message
    assert run(seeded, T0 + timedelta(days=1), sender, max_attempts=3) == []
    assert len(seeded.exec(select(Notification).where(Notification.kind == "email_failed")).all()) == 1


def test_a_permanent_refusal_fails_immediately_without_retrying(seeded):
    sender = FakeSender()
    sender.fail_next(1, retryable=False, message="550 no such user")
    event = queue(seeded, mk(seeded))
    run(seeded, T0, sender)
    seeded.refresh(event)
    assert (event.state, event.attempts) == ("failed", 1)
    assert len(seeded.exec(select(Notification).where(Notification.kind == "email_failed")).all()) == 1


def test_a_failed_email_can_be_retried_by_hand(seeded):
    sender = FakeSender()
    sender.fail_next(1, retryable=False)
    event = queue(seeded, mk(seeded))
    run(seeded, T0, sender)
    later = T0 + timedelta(hours=1)
    assert retry_failed(seeded, event, later) is True
    run(seeded, later, sender)
    seeded.refresh(event)
    assert event.state == "sent" and event.attempts == 1 and len(sender.sent) == 1
    assert retry_failed(seeded, event, later) is False, "only a failed email can be retried"


def test_a_bug_in_the_sender_is_a_retryable_failure_and_does_not_stop_the_batch(seeded):
    sender = FakeSender()
    sender.raise_next(RuntimeError("boom"))
    first, second = queue(seeded, mk(seeded, "A")), queue(seeded, mk(seeded, "B"))
    run(seeded, T0, sender)
    seeded.refresh(first), seeded.refresh(second)
    assert first.state == "pending" and first.last_error == "RuntimeError" and second.state == "sent"


# ---- claims: two workers never send the same row ----------------------------


def test_only_one_caller_can_claim_an_event(seeded):
    event = queue(seeded, mk(seeded))
    assert _claim(seeded, event.id, T0) is True
    assert _claim(seeded, event.id, T0) is False


def test_a_stale_claim_from_a_crashed_worker_is_given_back_after_ten_minutes(seeded):
    sender = FakeSender()
    event = queue(seeded, mk(seeded))
    assert _claim(seeded, event.id, T0)
    assert run(seeded, T0 + timedelta(minutes=9), sender) == [] and sender.sent == []
    assert len(run(seeded, T0 + timedelta(minutes=11), sender)) == 1 and len(sender.sent) == 1


# ---- reviewer visibility ----------------------------------------------------


def test_a_reviewer_who_is_a_user_also_gets_an_in_app_notification(seeded):
    task = mk(seeded, "Refund wording", reviewer="meera.nair@acme.example.com")
    queue(seeded, task, "completed")
    [note] = seeded.exec(select(Notification).where(Notification.user_id == MEERA_USER)).all()
    assert note.kind == "review_completed" and "Jiya Sharma completed" in note.message and "Refund wording" in note.message
    assert note.payload == {"task_id": task.id}


def test_an_outside_reviewer_gets_the_email_but_no_in_app_notification(seeded):
    queue(seeded, mk(seeded, reviewer="someone@outside.example"))
    assert seeded.exec(select(Notification).where(Notification.kind.like("review_%"))).all() == []
    assert events(seeded)[0].recipient == "someone@outside.example"


# ---- missed deadlines -------------------------------------------------------


def test_a_missed_deadline_queues_the_reviewer_email_in_the_same_pass(seeded):
    task = mk(seeded, deadline=ist(2, 12))
    run_tick(seeded, ist(2, 13), IST)
    [event] = events(seeded)
    assert (event.kind, event.state, event.task_id) == ("missed", "pending", task.id)
    sender = FakeSender()
    run(seeded, ist(2, 13), sender)
    assert sender.sent[0].subject.startswith("[TaskPilot] Missed deadline:")
    run_tick(seeded, ist(2, 14), IST)
    assert len(events(seeded)) == 1, "a task is only missed once, so only one email"


def test_missed_then_completed_late_sends_two_different_emails_once_each(seeded):
    task = mk(seeded, deadline=ist(2, 12))
    run_tick(seeded, ist(2, 13), IST)
    task.status, task.completed_at = "done", ist(2, 15)
    seeded.add(task)
    queue(seeded, task, "completed", now=ist(2, 15))
    queue(seeded, task, "completed", now=ist(2, 16))  # asking again changes nothing
    sender = FakeSender()
    run(seeded, ist(2, 16), sender)
    assert [m.subject.split(":")[0] for m in sender.sent] == ["[TaskPilot] Missed deadline", "[TaskPilot] Completed"]
    assert "Completed late" in sender.sent[1].text


def test_no_reviewer_means_no_email_when_a_deadline_is_missed(seeded):
    mk(seeded, reviewer=None, deadline=ist(2, 12))
    run_tick(seeded, ist(2, 13), IST)
    assert events(seeded) == []


def test_the_scheduler_tick_sends_due_emails_and_retries(seeded, engine):
    settings = Settings(_env_file=None, app_env="test", email_retry_base_seconds=60)
    sender = FakeSender()
    sender.fail_next(1)
    mk(seeded, deadline=ist(2, 12))
    clock_now = {"t": ist(2, 13).astimezone(UTC)}
    tick_once(engine, settings, lambda: clock_now["t"], sender)  # detects the miss, first send fails
    assert sender.sent == []
    clock_now["t"] += timedelta(seconds=61)
    tick_once(engine, settings, lambda: clock_now["t"], sender)  # the retry goes through
    assert len(sender.sent) == 1


def test_queue_event_keeps_the_recipient_lowercase_and_trimmed(seeded):
    task = mk(seeded)
    event = queue_event(seeded, task, "completed", "  Ravi.Kumar@ACME.example.com ", ("s", "t", "h"), T0)
    assert event.recipient == RAVI
