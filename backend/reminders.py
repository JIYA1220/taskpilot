"""Missed-deadline detection and the periodic reminder. Plain functions of (session, now):
no scheduler, no wall clock, so the tests drive them with a fake clock."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from backend.models import Notification, Task, User, UserPrefs

PENDING = ("todo", "in_progress", "blocked")  # not done and not missed
PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}
MAX_LISTED = 8
_EARLIEST = datetime.min.replace(tzinfo=UTC)  # sort placeholder for "no deadline"; never displayed


@dataclass
class TickResult:
    missed: list[Task] = field(default_factory=list)  # newly missed this tick (Milestone 3 emails reviewers from here)
    reminders: list[Notification] = field(default_factory=list)


def get_prefs(session: Session, user_id: int) -> UserPrefs:
    prefs = session.get(UserPrefs, user_id)
    if prefs is None:
        prefs = UserPrefs(user_id=user_id)
        session.add(prefs)
        session.flush()
    return prefs


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def is_working_time(now: datetime, prefs: UserPrefs, tz: ZoneInfo) -> bool:
    """Start is inclusive, end is exclusive. Overnight shifts are not supported."""
    local = now.astimezone(tz)
    return local.weekday() in prefs.work_days and parse_hhmm(prefs.work_start) <= local.time() < parse_hhmm(prefs.work_end)


def format_time_left(deadline: datetime | None, now: datetime) -> str:
    if deadline is None:
        return "no deadline"
    seconds = int((deadline - now).total_seconds())
    if seconds < 0:
        return "overdue"
    minutes = max(seconds // 60, 0)
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    if days:
        return f"due in {days}d {hours}h"
    if hours:
        return f"due in {hours}h {mins}m"
    return f"due in {mins}m" if mins else "due in under a minute"


def pending_tasks(session: Session, user_id: int) -> list[Task]:
    """P1 first; within a priority the nearest deadline first, tasks without a deadline last."""
    tasks = session.exec(select(Task).where(Task.owner_id == user_id, Task.status.in_(PENDING))).all()
    return sorted(
        tasks,
        key=lambda t: (PRIORITY_ORDER.get(t.priority, 1), t.deadline is None, t.deadline or _EARLIEST, t.id),
    )


def mark_missed(session: Session, now: datetime, tz: ZoneInfo) -> list[Task]:
    """A task whose deadline has passed without completion becomes `missed`. Happens once:
    a missed task is no longer pending, so later ticks skip it."""
    overdue = session.exec(
        select(Task).where(Task.status.in_(PENDING), Task.deadline.is_not(None), Task.deadline < now)
    ).all()
    for task in overdue:
        task.status = "missed"
        session.add(task)
        due = task.deadline.astimezone(tz).strftime("%a %d %b %H:%M")
        session.add(
            Notification(
                user_id=task.owner_id,
                kind="missed",
                message=f'Missed deadline: "{task.title}" was due {due}.',
                payload={"task_id": task.id},
                created_at=now,
            )
        )
    return list(overdue)


def build_reminder(user_id: int, tasks: list[Task], now: datetime) -> Notification:
    listed = tasks[:MAX_LISTED]
    lines = [f"- {t.priority} {t.title} ({format_time_left(t.deadline, now)})" for t in listed]
    if len(tasks) > len(listed):
        lines.append(f"+{len(tasks) - len(listed)} more")
    count = len(tasks)
    return Notification(
        user_id=user_id,
        kind="reminder",
        message=f"{count} pending task{'s' if count != 1 else ''}:\n" + "\n".join(lines),
        payload={
            "total": count,
            "tasks": [
                {"id": t.id, "title": t.title, "priority": t.priority, "time_left": format_time_left(t.deadline, now)}
                for t in listed
            ],
        },
        created_at=now,
    )


def run_tick(session: Session, now: datetime, tz: ZoneInfo) -> TickResult:
    """One scheduler pass at time `now` (aware). Safe to call as often as you like."""
    result = TickResult(missed=mark_missed(session, now, tz))
    session.flush()
    for user in session.exec(select(User)).all():
        prefs = get_prefs(session, user.id)
        if not prefs.reminders_enabled or not is_working_time(now, prefs, tz):
            continue
        if prefs.last_reminder_at and now - prefs.last_reminder_at < timedelta(minutes=prefs.interval_minutes):
            continue
        tasks = pending_tasks(session, user.id)
        if not tasks:
            continue
        reminder = build_reminder(user.id, tasks, now)
        session.add(reminder)
        prefs.last_reminder_at = now
        session.add(prefs)
        result.reminders.append(reminder)
    session.commit()
    return result
