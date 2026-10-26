"""Schedule conflicts, by plain rules. No model is involved: this is arithmetic on estimates
and deadlines, so it is deterministic, cheap and easy to explain.

Two rules:
  1. overload: the estimated minutes of today's open tasks exceed the working time left today.
  2. P1 clash: two or more P1 tasks have exactly the same deadline.

The code only *suggests* what to move (a P3 task first). The person decides."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.models import Task

ACTIVE = ("todo", "in_progress")  # work that can actually be done today (blocked tasks cannot)
HAS_DEADLINE = ("todo", "in_progress", "blocked")
SUGGEST_ORDER = ("P3", "P2")  # never suggest moving a P1


@dataclass
class Suggestion:
    task_id: int
    title: str
    priority: str
    estimated_minutes: int
    proposed_deadline: datetime  # the same time of day on the next working day


@dataclass
class ScheduleConflict:
    kind: str  # overload or p1_clash
    message: str
    task_ids: list[int]
    minutes_needed: int | None = None
    minutes_available: int | None = None
    suggestions: list[Suggestion] = field(default_factory=list)
    untimed_task_ids: list[int] = field(default_factory=list)  # due today but no estimate (counted as 0)


def _hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def remaining_minutes(now: datetime, work_start: str, work_end: str, work_days: list[int], tz: ZoneInfo) -> int:
    """Working minutes left today: 0 on a non-working day or after hours."""
    local = now.astimezone(tz)
    if local.weekday() not in work_days:
        return 0
    start = local.replace(hour=_hhmm(work_start).hour, minute=_hhmm(work_start).minute, second=0, microsecond=0)
    end = local.replace(hour=_hhmm(work_end).hour, minute=_hhmm(work_end).minute, second=0, microsecond=0)
    if local >= end:
        return 0
    return int((end - max(local, start)).total_seconds() // 60)


def next_working_day_same_time(deadline: datetime, work_days: list[int], tz: ZoneInfo) -> datetime:
    local = deadline.astimezone(tz)
    day = local.date()
    for _ in range(14):
        day += timedelta(days=1)
        if day.weekday() in work_days:
            return datetime.combine(day, local.timetz().replace(tzinfo=None), tzinfo=tz)
    raise ValueError("no working day in the next two weeks")


def _label(deadline: datetime, tz: ZoneInfo) -> str:
    return deadline.astimezone(tz).strftime("%a %d %b %H:%M")


def _overload(tasks: list[Task], now: datetime, work_start: str, work_end: str, work_days: list[int], tz: ZoneInfo) -> ScheduleConflict | None:
    today = now.astimezone(tz).date()
    due_today = [t for t in tasks if t.status in ACTIVE and t.deadline and t.deadline.astimezone(tz).date() == today]
    needed = sum(t.estimated_minutes or 0 for t in due_today)
    available = remaining_minutes(now, work_start, work_end, work_days, tz)
    if needed <= available:
        return None
    excess = needed - available
    untimed = [t.id for t in due_today if not t.estimated_minutes]

    suggestions: list[Suggestion] = []
    freed = 0
    for priority in SUGGEST_ORDER:  # P3 first; P2 only if the P3 tasks cannot free enough time
        movable = sorted(
            (t for t in due_today if t.priority == priority and t.estimated_minutes),
            key=lambda t: (-t.estimated_minutes, t.id),  # the biggest first, so fewer tasks have to move
        )
        for t in movable:
            if freed >= excess:
                break
            suggestions.append(Suggestion(t.id, t.title, t.priority, t.estimated_minutes, next_working_day_same_time(t.deadline, work_days, tz)))
            freed += t.estimated_minutes
    message = f"Today's tasks need {needed} min but only {available} min of working time are left."
    if freed < excess:
        message += " Even moving every P2 and P3 task would not make it fit."
    if untimed:
        message += f" {len(untimed)} task(s) due today have no estimate and are counted as 0 min."
    return ScheduleConflict(
        "overload", message, [t.id for t in due_today], needed, available, suggestions, untimed
    )


def _p1_clashes(tasks: list[Task], tz: ZoneInfo) -> list[ScheduleConflict]:
    groups: dict[datetime, list[Task]] = {}
    for t in tasks:
        if t.priority == "P1" and t.status in HAS_DEADLINE and t.deadline:
            groups.setdefault(t.deadline, []).append(t)
    conflicts = []
    for deadline, group in sorted(groups.items()):
        if len(group) < 2:
            continue
        group.sort(key=lambda t: (t.estimated_minutes or 10**6, t.id))
        titles = ", ".join(f'"{t.title}"' for t in group)
        message = f"{len(group)} P1 tasks are due at the same time ({_label(deadline, tz)}): {titles}."
        if group[0].estimated_minutes:
            message += f' Consider doing the shortest first ("{group[0].title}", {group[0].estimated_minutes} min).'
        conflicts.append(ScheduleConflict("p1_clash", message, [t.id for t in group]))
    return conflicts


def detect_schedule_conflicts(
    tasks: list[Task], now: datetime, work_start: str, work_end: str, work_days: list[int], tz: ZoneInfo
) -> list[ScheduleConflict]:
    conflicts = []
    overload = _overload(tasks, now, work_start, work_end, work_days, tz)
    if overload:
        conflicts.append(overload)
    return conflicts + _p1_clashes(tasks, tz)
