import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlmodel import Session, col, select

from backend.api.deps import get_current_user
from backend.config import Settings, get_settings
from backend.db import get_session
from backend.models import Notification, User
from backend.reminders import get_prefs

router = APIRouter(prefix="/api", tags=["notifications"])

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class NotificationOut(BaseModel):
    id: int
    kind: str
    message: str
    payload: dict | None
    read: bool
    created_at: datetime


class NotificationList(BaseModel):
    unread: int
    items: list[NotificationOut]


@router.get("/notifications", response_model=NotificationList)
def list_notifications(
    limit: int = 50, user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> NotificationList:
    limit = max(1, min(limit, 200))
    mine = Notification.user_id == user.id
    items = session.exec(select(Notification).where(mine).order_by(col(Notification.id).desc()).limit(limit)).all()
    unread = len(session.exec(select(Notification.id).where(mine, Notification.read == False)).all())  # noqa: E712
    return NotificationList(unread=unread, items=[NotificationOut.model_validate(n, from_attributes=True) for n in items])


@router.post("/notifications/read-all", status_code=204)
def read_all(user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> None:
    for n in session.exec(select(Notification).where(Notification.user_id == user.id, Notification.read == False)):  # noqa: E712
        n.read = True
        session.add(n)
    session.commit()


@router.post("/notifications/{notification_id}/read", status_code=204)
def read_one(notification_id: int, user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> None:
    n = session.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(404, "No such notification")
    n.read = True
    session.add(n)
    session.commit()


class PrefsIn(BaseModel):
    reminders_enabled: bool
    interval_minutes: int = Field(ge=1, le=1440)
    work_start: str
    work_end: str
    work_days: list[int] = Field(min_length=1, max_length=7)

    @field_validator("work_start", "work_end")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        if not HHMM.match(value):
            raise ValueError("use HH:MM, for example 09:00")
        return value

    @field_validator("work_days")
    @classmethod
    def _days(cls, value: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in value) or len(set(value)) != len(value):
            raise ValueError("work_days must be unique numbers 0 (Monday) to 6 (Sunday)")
        return sorted(value)

    @model_validator(mode="after")
    def _order(self) -> "PrefsIn":
        if self.work_start >= self.work_end:
            raise ValueError("work_end must be later than work_start (overnight shifts are not supported)")
        return self


class PrefsOut(PrefsIn):
    timezone: str
    last_reminder_at: datetime | None


def _prefs_out(prefs, settings: Settings) -> PrefsOut:
    return PrefsOut(
        reminders_enabled=prefs.reminders_enabled,
        interval_minutes=prefs.interval_minutes,
        work_start=prefs.work_start,
        work_end=prefs.work_end,
        work_days=prefs.work_days,
        timezone=settings.timezone,
        last_reminder_at=prefs.last_reminder_at,
    )


@router.get("/settings/reminders", response_model=PrefsOut)
def read_prefs(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PrefsOut:
    prefs = get_prefs(session, user.id)
    session.commit()
    return _prefs_out(prefs, settings)


@router.put("/settings/reminders", response_model=PrefsOut)
def update_prefs(
    body: PrefsIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PrefsOut:
    prefs = get_prefs(session, user.id)
    for name, value in body.model_dump().items():
        setattr(prefs, name, value)
    session.add(prefs)
    session.commit()
    return _prefs_out(prefs, settings)
