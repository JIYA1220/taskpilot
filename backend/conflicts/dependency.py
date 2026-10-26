"""Dependencies between teams: detect, block, notify, resolve.

A task that needs something from another team becomes `blocked`. A task that another team
is waiting on gets a Dependency record and a notice, but is NOT blocked: the person can act
on it right now, so calling it blocked would be wrong. (Both are flagged red in the UI.)"""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from backend.config import Settings
from backend.conflicts.matching import MatchResult, match_team
from backend.directory import Directory
from backend.llm import GroqLLM
from backend.mail.outbox import queue_reviewer_event
from backend.models import Dependency, EmailEvent, Notification, Task, User

DIRECTIONS = ("need_from_them", "they_need_from_me")


def team_name(directory: Directory, team_id: str | None) -> str:
    for team in directory.teams:
        if team.id == team_id:
            return team.name
    return "another team"


def describe(directory: Directory, dep: Dependency) -> str:
    team = team_name(directory, dep.depends_on_team_id)
    what = dep.what_is_needed or "something"
    if dep.direction == "need_from_them":
        return f"waiting on {team} for {what}"
    return f"{team} is waiting on the owner for {what}"


def register_dependency(
    session: Session,
    task: Task,
    owner: User,
    *,
    direction: str,
    what: str,
    directory: Directory,
    now: datetime,
    tz: ZoneInfo,
    llm: GroqLLM | None = None,
    settings: Settings | None = None,
    hint_team: str | None = None,
    team_id: str | None = None,
) -> tuple[Dependency, EmailEvent | None]:
    """Record a dependency, block the task if it is waiting on someone, and tell the owner
    (and, for a blocked task, the reviewer). `team_id` is a person's explicit choice and skips matching."""
    hint = hint_team if hint_team and hint_team != "none" else None
    what = (what or task.title).strip()[:300]
    if team_id:
        match = MatchResult(team_id, "manual")
    else:
        text = " ".join(filter(None, [task.title, task.raw_text, what]))
        match = match_team(llm, settings, directory, owner.team_id, text, hint)

    dep = Dependency(
        task_id=task.id,
        depends_on_team_id=match.team_id,
        direction=direction,
        what_is_needed=what,
        needed_by=task.deadline,
        match_method=match.method,
        hint_team=hint,
        match_candidates=match.candidates,
        created_at=now,
    )
    session.add(dep)
    session.flush()

    team = team_name(directory, dep.depends_on_team_id)
    event = None
    if direction == "need_from_them":
        if task.status in ("todo", "in_progress"):
            task.status = "blocked"
            session.add(task)
        session.add(
            Notification(
                user_id=owner.id,
                kind="blocked",
                message=f'"{task.title}" is blocked: {describe(directory, dep)}. Add by when you need it and who you have already asked.',
                payload={"task_id": task.id, "dependency_id": dep.id},
                created_at=now,
            )
        )
        event = queue_reviewer_event(session, task, owner, "blocked", now, tz, detail=describe(directory, dep))
    else:
        session.add(
            Notification(
                user_id=owner.id,
                kind="dependency",
                message=f'{team} is waiting on you for "{what}" (task: {task.title}).',
                payload={"task_id": task.id, "dependency_id": dep.id},
                created_at=now,
            )
        )
    return dep, event


def open_blockers(session: Session, task_id: int) -> list[Dependency]:
    return list(
        session.exec(
            select(Dependency).where(
                Dependency.task_id == task_id, Dependency.direction == "need_from_them", Dependency.state == "open"
            )
        )
    )


def resolve_dependency(session: Session, dep: Dependency, task: Task, owner: User, directory: Directory, now: datetime) -> bool:
    """Mark a dependency resolved. The task is unblocked only when nothing else is blocking it."""
    if dep.state != "open":
        return False
    dep.state, dep.resolved_at = "resolved", now
    session.add(dep)
    session.flush()
    if task.status == "blocked" and not open_blockers(session, task.id):
        task.status = "todo"
        session.add(task)
        session.add(
            Notification(
                user_id=owner.id,
                kind="unblocked",
                message=f'"{task.title}" is unblocked: {team_name(directory, dep.depends_on_team_id)} has delivered {dep.what_is_needed or "what you needed"}.',
                payload={"task_id": task.id, "dependency_id": dep.id},
                created_at=now,
            )
        )
    return True
