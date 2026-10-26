from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, select

from backend.api.deps import get_clock, get_current_user, get_llm, get_sender
from backend.api.tasks import TaskOut
from backend.clock import Clock
from backend.config import Settings, get_settings
from backend.conflicts.dependency import register_dependency, resolve_dependency, team_name
from backend.conflicts.schedule import detect_schedule_conflicts
from backend.db import get_session
from backend.directory import load_directory
from backend.llm import GroqLLM
from backend.mail.outbox import process_outbox
from backend.mail.sender import EmailSender
from backend.models import Dependency, Task, User
from backend.reminders import get_prefs

router = APIRouter(prefix="/api", tags=["conflicts"])
OPEN_STATUSES = ("todo", "in_progress", "blocked")


class DependencyOut(BaseModel):
    id: int
    task_id: int
    depends_on_team_id: str | None
    team_name: str
    direction: str
    what_is_needed: str
    needed_by: datetime | None
    already_asked: str
    state: str
    match_method: str
    hint_team: str | None
    created_at: datetime
    resolved_at: datetime | None


class NewDependency(BaseModel):
    direction: Literal["need_from_them", "they_need_from_me"]
    what_is_needed: str = Field(min_length=1, max_length=300)
    team_id: str | None = None  # leave empty to let TaskPilot work out which team


class DependencyUpdate(BaseModel):
    what_is_needed: str = Field(min_length=1, max_length=300)
    needed_by: datetime | None = None
    already_asked: str = Field(default="", max_length=300)
    team_id: str | None = None

    @field_validator("needed_by")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("needed_by must include a UTC offset")
        return value


class SuggestionOut(BaseModel):
    task_id: int
    title: str
    priority: str
    estimated_minutes: int
    proposed_deadline: datetime


class ConflictOut(BaseModel):
    kind: str
    message: str
    task_ids: list[int]
    minutes_needed: int | None
    minutes_available: int | None
    suggestions: list[SuggestionOut]
    untimed_task_ids: list[int]


class DeadlineIn(BaseModel):
    deadline: datetime

    @field_validator("deadline")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("deadline must include a UTC offset")
        return value


def _own_task(session: Session, task_id: int, user: User) -> Task:
    task = session.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(404, "No such task")
    return task


def _own_dependency(session: Session, dep_id: int, user: User) -> tuple[Dependency, Task]:
    dep = session.get(Dependency, dep_id)
    task = session.get(Task, dep.task_id) if dep else None
    if dep is None or task is None or task.owner_id != user.id:
        raise HTTPException(404, "No such dependency")
    return dep, task


def _check_team(team_id: str | None, user: User, settings: Settings) -> None:
    if team_id is None:
        return
    directory = load_directory(settings.directory_path)
    if team_id not in directory.team_ids():
        raise HTTPException(422, "Unknown team")
    if team_id == user.team_id:
        raise HTTPException(422, "A dependency is on another team, not your own")


def _out(dep: Dependency, settings: Settings) -> DependencyOut:
    directory = load_directory(settings.directory_path)
    return DependencyOut(
        **dep.model_dump(exclude={"match_candidates"}),
        team_name=team_name(directory, dep.depends_on_team_id) if dep.depends_on_team_id else "Team not identified yet",
    )


@router.get("/dependencies", response_model=list[DependencyOut])
def list_dependencies(
    state: Literal["open", "resolved", "all"] = "all",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[DependencyOut]:
    own = select(Task.id).where(Task.owner_id == user.id)
    query = select(Dependency).where(Dependency.task_id.in_(own))
    if state != "all":
        query = query.where(Dependency.state == state)
    return [_out(d, settings) for d in session.exec(query.order_by(Dependency.id))]


@router.post("/tasks/{task_id}/dependencies", response_model=DependencyOut, status_code=201)
def add_dependency(
    task_id: int,
    body: NewDependency,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    llm: GroqLLM = Depends(get_llm),
    sender: EmailSender = Depends(get_sender),
    clock: Clock = Depends(get_clock),
) -> DependencyOut:
    """For a dependency the AI did not notice."""
    task = _own_task(session, task_id, user)
    if task.status not in OPEN_STATUSES:
        raise HTTPException(409, "Only an open task can get a dependency")
    _check_team(body.team_id, user, settings)
    now, tz = clock(), ZoneInfo(settings.timezone)
    dep, event = register_dependency(
        session, task, user, direction=body.direction, what=body.what_is_needed, team_id=body.team_id,
        directory=load_directory(settings.directory_path), now=now, tz=tz, llm=llm, settings=settings,
    )
    session.commit()
    if event is not None:
        process_outbox(session, now, sender, tz, max_attempts=settings.email_max_attempts, retry_base_seconds=settings.email_retry_base_seconds, only_ids=[event.id])
    session.refresh(dep)
    return _out(dep, settings)


@router.put("/dependencies/{dep_id}", response_model=DependencyOut)
def update_dependency(
    dep_id: int,
    body: DependencyUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DependencyOut:
    """The follow-up answers: what exactly is needed, by when, who has already been asked."""
    dep, _ = _own_dependency(session, dep_id, user)
    if dep.state != "open":
        raise HTTPException(409, "This dependency is already resolved")
    _check_team(body.team_id, user, settings)
    dep.what_is_needed, dep.needed_by, dep.already_asked = body.what_is_needed.strip(), body.needed_by, body.already_asked.strip()
    if body.team_id != dep.depends_on_team_id:
        dep.depends_on_team_id = body.team_id
        dep.match_method = "manual"
    session.add(dep)
    session.commit()
    session.refresh(dep)
    return _out(dep, settings)


@router.post("/dependencies/{dep_id}/resolve", response_model=DependencyOut)
def resolve(
    dep_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> DependencyOut:
    dep, task = _own_dependency(session, dep_id, user)
    if not resolve_dependency(session, dep, task, user, load_directory(settings.directory_path), clock()):
        raise HTTPException(409, "This dependency is already resolved")
    session.commit()
    session.refresh(dep)
    return _out(dep, settings)


@router.get("/conflicts", response_model=list[ConflictOut])
def schedule_conflicts(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    clock: Clock = Depends(get_clock),
) -> list[ConflictOut]:
    tasks = list(session.exec(select(Task).where(Task.owner_id == user.id)))
    prefs = get_prefs(session, user.id)
    session.commit()
    found = detect_schedule_conflicts(tasks, clock(), prefs.work_start, prefs.work_end, prefs.work_days, ZoneInfo(settings.timezone))
    return [ConflictOut(**{**c.__dict__, "suggestions": [SuggestionOut(**s.__dict__) for s in c.suggestions]}) for c in found]


@router.post("/tasks/{task_id}/deadline", response_model=TaskOut)
def move_deadline(
    task_id: int,
    body: DeadlineIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
) -> Task:
    """The person's decision to act on a suggestion (or to reschedule for any other reason)."""
    task = _own_task(session, task_id, user)
    if task.status not in OPEN_STATUSES:
        raise HTTPException(409, "Only an open task can be rescheduled")
    if body.deadline <= clock():
        raise HTTPException(422, "The new deadline must be in the future")
    task.deadline = body.deadline
    session.add(task)
    session.commit()
    session.refresh(task)
    return task
