"""Reminder timing, missed-deadline detection and the working-hours rule, all with a fake clock."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select

from backend.models import Notification, Task, UserPrefs
from backend.reminders import MAX_LISTED, format_time_left, get_prefs, is_working_time, run_tick

IST = ZoneInfo("Asia/Kolkata")
JIYA, AARAV = 1, 2  # seeded user ids (see eval/directory.json order)


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    """November 2026 in India. Monday 2 Nov is day 2."""
    return datetime(2026, 11, day, hour, minute, tzinfo=IST)


def task(session, title="t", owner=JIYA, priority="P2", deadline=None, status="todo") -> Task:
    t = Task(owner_id=owner, title=title, priority=priority, deadline=deadline, status=status)
    session.add(t)
    session.commit()
    return t


def tick(session, now):
    return run_tick(session, now, IST)


def notifications(session, kind=None, user=JIYA):
    q = select(Notification).where(Notification.user_id == user)
    if kind:
        q = q.where(Notification.kind == kind)
    return session.exec(q.order_by(Notification.id)).all()


# ---- missed deadlines -------------------------------------------------------


def test_overdue_task_becomes_missed_once_with_a_notification(seeded):
    t = task(seeded, "Send report", deadline=ist(2, 17))
    result = tick(seeded, ist(2, 17, 1))
    assert [m.id for m in result.missed] == [t.id]
    seeded.refresh(t)
    assert t.status == "missed"
    [n] = notifications(seeded, "missed")
    assert n.message == 'Missed deadline: "Send report" was due Mon 02 Nov 17:00.'
    assert n.payload == {"task_id": t.id} and not n.read
    assert tick(seeded, ist(2, 17, 5)).missed == []
    assert len(notifications(seeded, "missed")) == 1, "a missed task is reported exactly once"


@pytest.mark.parametrize("status", ["todo", "in_progress", "blocked"])
def test_every_unfinished_status_can_become_missed(seeded, status):
    t = task(seeded, deadline=ist(2, 10), status=status)
    tick(seeded, ist(2, 11))
    seeded.refresh(t)
    assert t.status == "missed"


def test_done_missed_and_no_deadline_tasks_are_left_alone(seeded):
    done = task(seeded, "done", deadline=ist(2, 10), status="done")
    already = task(seeded, "already", deadline=ist(2, 10), status="missed")
    nodeadline = task(seeded, "open-ended")
    future = task(seeded, "future", deadline=ist(2, 20))
    assert tick(seeded, ist(2, 12)).missed == []
    for t, expected in ((done, "done"), (already, "missed"), (nodeadline, "todo"), (future, "todo")):
        seeded.refresh(t)
        assert t.status == expected
    assert notifications(seeded, "missed") == []


def test_deadline_exactly_now_is_not_missed_but_one_second_later_is(seeded):
    t = task(seeded, deadline=ist(2, 17))
    assert tick(seeded, ist(2, 17)).missed == []
    assert [m.id for m in tick(seeded, ist(2, 17) + timedelta(seconds=1)).missed] == [t.id]


def test_missed_detection_ignores_working_hours(seeded):
    t = task(seeded, deadline=ist(3, 2))  # 02:00 on Tuesday
    tick(seeded, ist(3, 3))
    seeded.refresh(t)
    assert t.status == "missed"


def test_missed_notification_goes_to_the_owner_only(seeded):
    task(seeded, owner=AARAV, deadline=ist(2, 10))
    tick(seeded, ist(2, 11))
    assert len(notifications(seeded, "missed", AARAV)) == 1
    assert notifications(seeded, "missed", JIYA) == []


# ---- reminder timing --------------------------------------------------------


def test_first_reminder_lists_p1_first_then_nearest_deadline_then_no_deadline(seeded):
    task(seeded, "low no date", priority="P3")
    task(seeded, "normal soon", priority="P2", deadline=ist(2, 11))
    task(seeded, "urgent later", priority="P1", deadline=ist(2, 17, 30))
    task(seeded, "urgent sooner", priority="P1", deadline=ist(2, 12, 10))
    task(seeded, "normal no date", priority="P2")
    [r] = tick(seeded, ist(2, 10)).reminders
    lines = r.message.splitlines()
    assert lines[0] == "5 pending tasks:"
    assert lines[1:] == [
        "- P1 urgent sooner (due in 2h 10m)",
        "- P1 urgent later (due in 7h 30m)",
        "- P2 normal soon (due in 1h 0m)",
        "- P2 normal no date (no deadline)",
        "- P3 low no date (no deadline)",
    ]
    assert r.kind == "reminder" and r.payload["total"] == 5
    assert [t["title"] for t in r.payload["tasks"]][0] == "urgent sooner"


def test_reminder_repeats_only_after_the_interval(seeded):
    task(seeded, "a")
    assert len(tick(seeded, ist(2, 10)).reminders) == 1
    assert tick(seeded, ist(2, 10, 59)).reminders == []
    assert len(tick(seeded, ist(2, 11)).reminders) == 1  # exactly 60 minutes later
    assert tick(seeded, ist(2, 11, 30)).reminders == []
    assert len(notifications(seeded, "reminder")) == 2
    assert get_prefs(seeded, JIYA).last_reminder_at == ist(2, 11)


def test_interval_is_configurable(seeded):
    task(seeded, "a")
    prefs = get_prefs(seeded, JIYA)
    prefs.interval_minutes = 1
    seeded.add(prefs)
    seeded.commit()
    counts = [len(tick(seeded, ist(2, 10) + timedelta(seconds=30 * i)).reminders) for i in range(5)]
    assert counts == [1, 0, 1, 0, 1]  # one a minute, ticks every 30 seconds


def test_disabled_reminders_send_nothing(seeded):
    task(seeded, "a")
    prefs = get_prefs(seeded, JIYA)
    prefs.reminders_enabled = False
    seeded.add(prefs)
    seeded.commit()
    assert tick(seeded, ist(2, 10)).reminders == []


def test_no_pending_tasks_means_no_reminder_and_the_clock_is_not_reset(seeded):
    done = task(seeded, "done", status="done")
    task(seeded, "missed", status="missed")
    assert tick(seeded, ist(2, 10)).reminders == []
    assert get_prefs(seeded, JIYA).last_reminder_at is None
    task(seeded, "new")  # a task added later is reminded about at the very next tick
    assert len(tick(seeded, ist(2, 10, 1)).reminders) == 1
    assert done.status == "done"


def test_finished_tasks_stop_appearing(seeded):
    a, b = task(seeded, "a"), task(seeded, "b")
    tick(seeded, ist(2, 10))
    a.status = "done"
    seeded.add(a)
    seeded.commit()
    [r] = tick(seeded, ist(2, 11)).reminders
    assert r.message.startswith("1 pending task:") and "- P2 b" in r.message and "a (" not in r.message
    assert b.id


def test_blocked_tasks_are_still_pending(seeded):
    task(seeded, "waiting on payments", status="blocked")
    assert len(tick(seeded, ist(2, 10)).reminders) == 1


def test_long_lists_are_truncated(seeded):
    for i in range(MAX_LISTED + 3):
        task(seeded, f"task {i}")
    [r] = tick(seeded, ist(2, 10)).reminders
    assert r.message.splitlines()[0] == f"{MAX_LISTED + 3} pending tasks:"
    assert r.message.splitlines()[-1] == "+3 more" and len(r.payload["tasks"]) == MAX_LISTED


def test_the_same_instant_twice_sends_one_reminder(seeded):
    task(seeded, "a")
    tick(seeded, ist(2, 10))
    tick(seeded, ist(2, 10))
    assert len(notifications(seeded, "reminder")) == 1


def test_each_user_gets_only_their_own_tasks(seeded):
    task(seeded, "jiya's", owner=JIYA)
    task(seeded, "aarav's", owner=AARAV)
    tick(seeded, ist(2, 10))
    assert "jiya's" in notifications(seeded, "reminder", JIYA)[0].message
    assert "aarav's" not in notifications(seeded, "reminder", JIYA)[0].message
    assert "aarav's" in notifications(seeded, "reminder", AARAV)[0].message


# ---- working hours ----------------------------------------------------------


@pytest.mark.parametrize(
    "when,expected",
    [
        (ist(2, 8, 59), False),  # a minute before start
        (ist(2, 9, 0), True),  # start is inclusive
        (ist(2, 17, 59), True),
        (ist(2, 18, 0), False),  # end is exclusive
        (ist(2, 23, 0), False),
        (ist(6, 12, 0), True),  # Friday
        (ist(7, 12, 0), False),  # Saturday
        (ist(8, 12, 0), False),  # Sunday
        (ist(9, 12, 0), True),  # next Monday
    ],
)
def test_default_working_hours(when, expected):
    assert is_working_time(when, UserPrefs(user_id=1), IST) is expected


def test_working_hours_are_evaluated_in_the_app_timezone_not_utc():
    prefs = UserPrefs(user_id=1)
    assert is_working_time(datetime(2026, 11, 2, 3, 30, tzinfo=UTC), prefs, IST)  # 09:00 IST
    assert is_working_time(datetime(2026, 11, 2, 9, 0, tzinfo=UTC), prefs, IST)  # 14:30 IST
    assert not is_working_time(datetime(2026, 11, 2, 13, 0, tzinfo=UTC), prefs, IST)  # 18:30 IST
    assert not is_working_time(datetime(2026, 11, 2, 3, 0, tzinfo=UTC), prefs, IST)  # 08:30 IST


def test_no_reminders_outside_working_hours(seeded):
    task(seeded, "a")
    for when in (ist(2, 8, 59), ist(2, 18, 0), ist(2, 22, 0), ist(7, 12, 0)):
        assert tick(seeded, when).reminders == [], when
    assert len(tick(seeded, ist(2, 9, 0)).reminders) == 1


def test_working_hours_and_days_are_configurable(seeded):
    task(seeded, "a")
    prefs = get_prefs(seeded, JIYA)
    prefs.work_start, prefs.work_end, prefs.work_days = "06:00", "08:00", [5]  # Saturdays, early
    seeded.add(prefs)
    seeded.commit()
    assert tick(seeded, ist(2, 7)).reminders == []  # Monday
    assert tick(seeded, ist(7, 9)).reminders == []  # Saturday, too late
    assert len(tick(seeded, ist(7, 7)).reminders) == 1


def test_reminders_resume_at_the_start_of_the_next_working_day(seeded):
    task(seeded, "a")
    assert len(tick(seeded, ist(2, 17, 30)).reminders) == 1
    assert tick(seeded, ist(2, 18, 30)).reminders == []
    assert tick(seeded, ist(3, 8, 59)).reminders == []
    assert len(tick(seeded, ist(3, 9, 0)).reminders) == 1  # overdue interval, so straight away


def test_friday_evening_reminder_resumes_on_monday(seeded):
    task(seeded, "a")
    assert len(tick(seeded, ist(6, 17, 0)).reminders) == 1
    assert tick(seeded, ist(7, 10)).reminders == []
    assert tick(seeded, ist(8, 10)).reminders == []
    assert len(tick(seeded, ist(9, 9, 0)).reminders) == 1


# ---- time left --------------------------------------------------------------


@pytest.mark.parametrize(
    "delta,text",
    [
        (None, "no deadline"),
        (timedelta(seconds=-1), "overdue"),
        (timedelta(seconds=20), "due in under a minute"),
        (timedelta(minutes=45), "due in 45m"),
        (timedelta(hours=2, minutes=10), "due in 2h 10m"),
        (timedelta(hours=1), "due in 1h 0m"),
        (timedelta(days=1, hours=3, minutes=40), "due in 1d 3h"),
    ],
)
def test_format_time_left(delta, text):
    now = ist(2, 9)
    assert format_time_left(None if delta is None else now + delta, now) == text
