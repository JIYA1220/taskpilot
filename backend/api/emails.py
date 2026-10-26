from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlmodel import Session, select

from backend.api.deps import get_clock, get_current_user, get_llm, get_sender
from backend.clock import Clock
from backend.config import Settings, get_settings
from backend.db import get_session
from backend.directory import load_directory
from backend.llm import GroqLLM
from backend.mail import render
from backend.mail.drafting import ATTACH_PLACEHOLDER, generate_draft, is_org_address
from backend.mail.outbox import process_outbox, queue_event, retry_failed
from backend.mail.sender import EmailSender
from backend.models import EmailDraft, EmailEvent, EmailPrefs, Task, User

router = APIRouter(prefix="/api", tags=["email"])


class DraftOut(BaseModel):
    id: int
    task_id: int
    to_email: str | None
    to_source: str
    subject: str
    body: str
    state: str
    needs_manual_review: bool


class DraftIn(BaseModel):
    to_email: EmailStr | None = None
    subject: str = Field(max_length=150)
    body: str = Field(max_length=4000)

    @field_validator("subject")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("subject must be a single line")
        return value.strip()


class EmailEventOut(BaseModel):
    id: int
    task_id: int
    kind: str
    recipient: str
    subject: str
    state: str
    attempts: int
    next_attempt_at: datetime
    last_error: str
    sent_at: datetime | None


class EmailSettings(BaseModel):
    auto_send_enabled: bool


class EmailSettingsOut(EmailSettings):
    org_domain: str


def _own_task(session: Session, task_id: int, user: User) -> Task:
    task = session.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(404, "No such task")
    return task


def _latest_draft(session: Session, task_id: int) -> EmailDraft | None:
    return session.exec(select(EmailDraft).where(EmailDraft.task_id == task_id).order_by(EmailDraft.id.desc())).first()


def _approve(
    session: Session, draft: EmailDraft, task: Task, owner: User, now: datetime, sender: EmailSender, settings: Settings
) -> EmailDraft:
    """The only path that turns a draft into a real email. Everything that must be true first is checked here."""
    if draft.state != "draft":
        raise HTTPException(409, "Only a draft that is waiting for approval can be approved")
    if not draft.to_email:
        raise HTTPException(409, "Choose who to send this to first")
    if not draft.subject.strip() or not draft.body.strip():
        raise HTTPException(409, "The subject and body cannot be empty")
    if ATTACH_PLACEHOLDER in draft.body.lower():
        raise HTTPException(409, "Remove the [Attach: ...] line first. TaskPilot cannot attach files.")
    tz = ZoneInfo(settings.timezone)
    event = queue_event(session, task, "draft", draft.to_email, render.draft_email(draft, owner), now)
    if event is None:
        raise HTTPException(409, "An email for this task has already been queued")
    draft.state = "approved"
    session.add(draft)
    session.commit()
    process_outbox(
        session, now, sender, tz,
        max_attempts=settings.email_max_attempts, retry_base_seconds=settings.email_retry_base_seconds, only_ids=[event.id],
    )
    session.refresh(draft)
    return draft


@router.post("/tasks/{task_id}/draft", response_model=DraftOut, status_code=201)
def create_draft(
    task_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    llm: GroqLLM = Depends(get_llm),
    sender: EmailSender = Depends(get_sender),
    clock: Clock = Depends(get_clock),
) -> EmailDraft:
    task = _own_task(session, task_id, user)
    if task.task_type != "email":
        raise HTTPException(409, "Only email tasks have an email draft")
    if task.status == "done":
        raise HTTPException(409, "This task is already done")
    current = _latest_draft(session, task_id)
    if current and current.state in ("draft", "approved", "sent"):
        raise HTTPException(409, "This task already has a draft. Edit or reject it first.")
    directory = load_directory(settings.directory_path)
    draft = generate_draft(llm, settings, directory, task, user)
    session.add(draft)
    session.commit()
    session.refresh(draft)

    prefs = session.get(EmailPrefs, user.id)
    if (
        prefs and prefs.auto_send_enabled and draft.to_email and not draft.needs_manual_review
        and is_org_address(draft.to_email, settings.org_domain)  # auto-send never leaves the organization
    ):
        try:
            draft = _approve(session, draft, task, user, clock(), sender, settings)
        except HTTPException:
            pass  # e.g. an [Attach] placeholder: leave it as a draft for the person to handle
    return draft


@router.get("/tasks/{task_id}/draft", response_model=DraftOut | None)
def read_draft(task_id: int, user: User = Depends(get_current_user), session: Session = Depends(get_session)):
    _own_task(session, task_id, user)
    return _latest_draft(session, task_id)


@router.put("/tasks/{task_id}/draft", response_model=DraftOut)
def edit_draft(
    task_id: int,
    body: DraftIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> EmailDraft:
    _own_task(session, task_id, user)
    draft = _latest_draft(session, task_id)
    if draft is None or draft.state != "draft":
        raise HTTPException(409, "There is no draft waiting for approval")
    draft.subject, draft.body = body.subject, body.body
    if body.to_email:
        address = str(body.to_email).lower()
        in_directory = address in {u.email.lower() for u in load_directory(settings.directory_path).users}
        draft.to_email, draft.to_source = address, "directory" if in_directory else "user"
    else:
        draft.to_email, draft.to_source = None, ""
    if draft.body.strip():
        draft.needs_manual_review = False  # the person has now written or reviewed it
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


@router.post("/tasks/{task_id}/draft/reject", response_model=DraftOut)
def reject_draft(task_id: int, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> EmailDraft:
    _own_task(session, task_id, user)
    draft = _latest_draft(session, task_id)
    if draft is None or draft.state != "draft":
        raise HTTPException(409, "There is no draft waiting for approval")
    draft.state = "rejected"
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


@router.post("/tasks/{task_id}/draft/approve", response_model=DraftOut)
def approve_draft(
    task_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    sender: EmailSender = Depends(get_sender),
    clock: Clock = Depends(get_clock),
) -> EmailDraft:
    task = _own_task(session, task_id, user)
    draft = _latest_draft(session, task_id)
    if draft is None:
        raise HTTPException(409, "There is no draft to approve")
    return _approve(session, draft, task, user, clock(), sender, settings)


@router.get("/emails", response_model=list[EmailEventOut])
def list_emails(user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> list[EmailEvent]:
    own = select(Task.id).where(Task.owner_id == user.id)
    return list(session.exec(select(EmailEvent).where(EmailEvent.task_id.in_(own)).order_by(EmailEvent.id.desc()).limit(100)))


@router.post("/emails/{event_id}/retry", response_model=EmailEventOut)
def retry_email(
    event_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    sender: EmailSender = Depends(get_sender),
    clock: Clock = Depends(get_clock),
) -> EmailEvent:
    event = session.get(EmailEvent, event_id)
    task = session.get(Task, event.task_id) if event else None
    if event is None or task is None or task.owner_id != user.id:
        raise HTTPException(404, "No such email")
    now = clock()
    if not retry_failed(session, event, now):
        raise HTTPException(409, "Only an email that has failed can be retried")
    process_outbox(
        session, now, sender, ZoneInfo(settings.timezone),
        max_attempts=settings.email_max_attempts, retry_base_seconds=settings.email_retry_base_seconds, only_ids=[event.id],
    )
    session.refresh(event)
    return event


@router.get("/settings/email", response_model=EmailSettingsOut)
def read_email_settings(
    user: User = Depends(get_current_user), session: Session = Depends(get_session), settings: Settings = Depends(get_settings)
) -> EmailSettingsOut:
    prefs = session.get(EmailPrefs, user.id)
    return EmailSettingsOut(auto_send_enabled=bool(prefs and prefs.auto_send_enabled), org_domain=settings.org_domain)


@router.put("/settings/email", response_model=EmailSettingsOut)
def update_email_settings(
    body: EmailSettings,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> EmailSettingsOut:
    prefs = session.get(EmailPrefs, user.id) or EmailPrefs(user_id=user.id)
    prefs.auto_send_enabled = body.auto_send_enabled
    session.add(prefs)
    session.commit()
    return EmailSettingsOut(auto_send_enabled=prefs.auto_send_enabled, org_domain=settings.org_domain)
