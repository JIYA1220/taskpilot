from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlmodel import Session, select

from backend.api.deps import get_clock, get_current_user
from backend.clock import Clock
from backend.config import Settings, get_settings
from backend.db import get_session
from backend.directory import load_directory
from backend.models import ClarifyingQuestion, Task, User
from backend.organize.schemas import Clarification, DependencyHint, Priority, TaskType

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskIn(BaseModel):
    """A reviewed (and possibly edited) task, as the person saves it."""

    title: str = Field(min_length=1, max_length=160)
    source_text: str = ""
    task_type: TaskType = "normal"
    problem_statement: str = ""
    priority: Priority = "P2"
    priority_reason: str = ""
    estimated_minutes: int | None = Field(default=None, ge=1, le=960)
    deadline: datetime | None = None
    reviewer_email: EmailStr | None = None
    dependency_hint: DependencyHint | None = None
    needs_manual_review: bool = False
    clarifications: list[Clarification] = Field(default_factory=list)

    @field_validator("deadline")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("deadline must include a UTC offset")
        return value


class SaveTasksRequest(BaseModel):
    tasks: list[TaskIn] = Field(min_length=1, max_length=20)


class TaskOut(BaseModel):
    id: int
    title: str
    task_type: str
    problem_statement: str
    priority: str
    priority_reason: str
    estimated_minutes: int | None
    deadline: datetime | None
    status: str
    reviewer_email: str | None
    dependency_hint: dict | None
    needs_manual_review: bool
    created_at: datetime
    completed_at: datetime | None


@router.post("", response_model=list[TaskOut], status_code=201)
def save_tasks(
    body: SaveTasksRequest,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[Task]:
    team_ids = load_directory(settings.directory_path).team_ids()
    for item in body.tasks:
        if item.dependency_hint:  # same team-id rule as for model output
            try:
                DependencyHint.model_validate(item.dependency_hint.model_dump(), context={"team_ids": team_ids})
            except ValueError as exc:
                raise HTTPException(422, "dependency_hint.team is not a known team") from exc
    saved = []
    for item in body.tasks:
        task = Task(
            owner_id=user.id,
            raw_text=item.source_text,
            title=item.title.strip(),
            problem_statement=item.problem_statement,
            priority=item.priority,
            priority_reason=item.priority_reason,
            estimated_minutes=item.estimated_minutes,
            deadline=item.deadline,
            task_type=item.task_type,
            reviewer_email=str(item.reviewer_email).lower() if item.reviewer_email else None,
            dependency_hint=item.dependency_hint.model_dump() if item.dependency_hint else None,
            needs_manual_review=item.needs_manual_review,
        )
        session.add(task)
        session.flush()
        for c in item.clarifications:
            session.add(ClarifyingQuestion(task_id=task.id, question=c.question, answer=c.answer))
        saved.append(task)
    session.commit()
    for task in saved:
        session.refresh(task)
    return saved


class StatusIn(BaseModel):
    # `blocked` and `missed` are set by the system (dependencies, deadlines), never by hand.
    status: Literal["todo", "in_progress", "done"]


@router.post("/{task_id}/status", response_model=TaskOut)
def set_status(
    task_id: int,
    body: StatusIn,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    clock: Clock = Depends(get_clock),
) -> Task:
    task = session.get(Task, task_id)
    if task is None or task.owner_id != user.id:
        raise HTTPException(404, "No such task")
    if task.status == "missed" and body.status != "done":
        raise HTTPException(409, "The deadline has passed; you can only mark a missed task as done")
    if task.status == "blocked" and body.status != "done":
        raise HTTPException(409, "This task is blocked; resolve its dependency first")
    task.status = body.status
    task.completed_at = clock() if body.status == "done" else None
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


@router.get("", response_model=list[TaskOut])
def list_tasks(user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> list[Task]:
    return list(session.exec(select(Task).where(Task.owner_id == user.id).order_by(Task.priority, Task.id)))
