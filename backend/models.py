from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel


class UTCDateTime(TypeDecorator):
    """Stores aware datetimes as naive UTC (SQLite has no tz) and returns aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: pass a timezone-aware value")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Team(SQLModel, table=True):
    # Slug ids ("payments") instead of integers: they are what the LLM's JSON
    # schema enum and the gold labels refer to, so they must stay readable.
    id: str = Field(primary_key=True)
    name: str
    description: str = ""
    owns: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    email: str = Field(unique=True, index=True)
    password_hash: str
    team_id: str = Field(foreign_key="team.id")


class Task(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id", index=True)
    raw_text: str = ""
    title: str
    problem_statement: str = ""
    priority: str = "P2"  # P1 to P3
    priority_reason: str = ""
    estimated_minutes: int | None = None
    deadline: datetime | None = Field(default=None, sa_column=Column(UTCDateTime, nullable=True))
    status: str = "todo"  # todo, in_progress, blocked, done, missed
    task_type: str = "normal"  # normal, email
    reviewer_email: str | None = None
    # Step A's dependency guess, kept for Milestone 4 (no Dependency rows yet).
    dependency_hint: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    needs_manual_review: bool = False
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )
    completed_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime, nullable=True)
    )


class ClarifyingQuestion(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    task_id: int = Field(foreign_key="task.id", index=True)
    question: str
    answer: str | None = None


class UserPrefs(SQLModel, table=True):
    """Reminder settings. A user without a row gets these defaults when first needed."""

    user_id: int = Field(foreign_key="user.id", primary_key=True)
    reminders_enabled: bool = True
    interval_minutes: int = 60
    work_start: str = "09:00"  # local time in the app timezone, HH:MM
    work_end: str = "18:00"
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4], sa_column=Column(JSON, nullable=False))  # 0 = Monday
    last_reminder_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime, nullable=True)
    )


class Notification(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    kind: str  # reminder, missed
    message: str
    payload: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    read: bool = False
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )


class AuditLog(SQLModel, table=True):
    """One row per LLM attempt (and, from Milestone 3, per email sent). Never holds keys."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )
    kind: str = "llm"  # llm, email
    model: str = ""
    prompt_name: str = ""
    prompt_version: str = ""
    attempt: int = 1
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    reasoning_tokens: int | None = None
    parsed_ok: bool | None = None
    detail: str = ""
