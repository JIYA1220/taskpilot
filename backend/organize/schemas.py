from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, ValidationInfo, field_validator

UnknownField = Literal["priority", "deadline", "dependency", "reviewer", "scope"]
TaskType = Literal["normal", "email"]
Priority = Literal["P1", "P2", "P3"]


def _require_offset(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("deadline must include a UTC offset, e.g. +05:30")
    return value


class Unknown(BaseModel):
    field: UnknownField
    question: str = Field(min_length=3, max_length=200)
    suggestions: list[str] = Field(default_factory=list)

    @field_validator("suggestions")
    @classmethod
    def _trim(cls, value: list[str]) -> list[str]:
        return [s.strip()[:60] for s in value if s.strip()][:4]


class DependencyHint(BaseModel):
    direction: Literal["none", "need_from_them", "they_need_from_me"] = "none"
    team: str = "none"
    what_is_needed: str = ""

    @field_validator("team")
    @classmethod
    def _team_in_directory(cls, value: str, info: ValidationInfo) -> str:
        # At the model and API boundaries the context carries the directory's team ids
        # and only those, "unknown" or "none" are accepted. Internal re-reads of
        # already-validated state pass no context and are left as they are.
        team_ids = (info.context or {}).get("team_ids")
        if team_ids is None:
            return value
        allowed = set(team_ids) | {"none", "unknown"}
        if value not in allowed:
            raise ValueError(f"team must be one of {sorted(allowed)}")
        return value


class ExtractedTask(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    source_text: str = ""
    task_type: TaskType = "normal"
    deadline: datetime | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=960)
    dependency: DependencyHint = Field(default_factory=DependencyHint)
    unknowns: list[Unknown] = Field(default_factory=list)
    needs_manual_review: bool = False  # set by code on fallback, never by the model

    @field_validator("deadline")
    @classmethod
    def _offset(cls, value: datetime | None) -> datetime | None:
        return _require_offset(value)


class ExtractOutput(BaseModel):
    tasks: list[ExtractedTask] = Field(min_length=1, max_length=20)


class RewrittenTask(BaseModel):
    index: int = Field(ge=0)
    goal: str = Field(min_length=1)
    definition_of_done: str = Field(min_length=1)
    inputs_needed: list[str] = Field(default_factory=list)
    priority: Priority
    priority_reason: str = Field(min_length=1, max_length=300)
    deadline: datetime | None = None

    @field_validator("deadline")
    @classmethod
    def _offset(cls, value: datetime | None) -> datetime | None:
        return _require_offset(value)


class RewriteOutput(BaseModel):
    tasks: list[RewrittenTask]

    @field_validator("tasks")
    @classmethod
    def _one_per_input(cls, value: list[RewrittenTask], info: ValidationInfo) -> list[RewrittenTask]:
        expected = (info.context or {}).get("n_tasks")
        if expected is not None and sorted(t.index for t in value) != list(range(expected)):
            raise ValueError(f"need exactly one entry for each index 0..{expected - 1}")
        return value


class Question(BaseModel):
    id: str
    task_index: int
    task_title: str
    field: UnknownField
    question: str
    suggestions: list[str]


class Clarification(BaseModel):
    question: str
    answer: str | None


class ProposedTask(BaseModel):
    """What the person reviews and may edit before saving."""

    source_text: str = ""
    title: str
    task_type: TaskType = "normal"
    deadline: datetime | None = None
    estimated_minutes: int | None = None
    priority: Priority = "P2"
    priority_reason: str = ""
    problem_statement: str = ""
    reviewer_email: EmailStr | None = None
    dependency_hint: DependencyHint = Field(default_factory=DependencyHint)
    needs_manual_review: bool = False
    clarifications: list[Clarification] = Field(default_factory=list)
