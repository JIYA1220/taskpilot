"""Schedule conflicts: pure rules, no database and no model."""

from datetime import timedelta

import pytest

from backend.conflicts.schedule import detect_schedule_conflicts, next_working_day_same_time, remaining_minutes
from backend.models import Task
from backend.tests.test_reminders import IST, ist

START, END, DAYS = "09:00", "18:00", [0, 1, 2, 3, 4]
MON_10 = ist(2, 10)  # 8 working hours (480 min) left on Monday 2 Nov


def t(id, priority="P2", minutes=60, due=None, status="todo", title=None) -> Task:
    return Task(id=id, owner_id=1, title=title or f"task {id}", priority=priority, estimated_minutes=minutes,
                deadline=due if due is not None else ist(2, 17), status=status)


def detect(tasks, now=MON_10, start=START, end=END, days=DAYS):
    return detect_schedule_conflicts(tasks, now, start, end, days, IST)


def kinds(conflicts):
    return [c.kind for c in conflicts]


# ---- working time left ------------------------------------------------------


@pytest.mark.parametrize(
    "now,expected",
    [
        (ist(2, 9, 0), 540),
        (ist(2, 10, 0), 480),
        (ist(2, 8, 0), 540),  # before the day starts: the whole day is left
        (ist(2, 17, 59), 1),
        (ist(2, 18, 0), 0),
        (ist(2, 22, 0), 0),
        (ist(7, 11, 0), 0),  # Saturday
        (ist(8, 11, 0), 0),  # Sunday
    ],
)
def test_remaining_working_minutes(now, expected):
    assert remaining_minutes(now, START, END, DAYS, IST) == expected


def test_remaining_minutes_respect_custom_hours_and_days():
    assert remaining_minutes(ist(7, 7, 0), "06:00", "10:00", [5], IST) == 180  # a Saturday shift
    assert remaining_minutes(ist(2, 7, 0), "06:00", "10:00", [5], IST) == 0


# ---- overload ---------------------------------------------------------------


def test_no_conflict_when_todays_work_fits():
    assert detect([t(1, minutes=240), t(2, minutes=240)]) == []  # exactly 480: fits
    assert detect([]) == []


def test_overload_reports_what_is_needed_against_what_is_left():
    [c] = detect([t(1, "P1", 300), t(2, "P2", 240), t(3, "P3", 60)])
    assert (c.kind, c.minutes_needed, c.minutes_available) == ("overload", 600, 480)
    assert c.task_ids == [1, 2, 3]
    assert c.message.startswith("Today's tasks need 600 min but only 480 min of working time are left.")


def test_only_todays_open_tasks_count():
    tasks = [
        t(1, minutes=300),
        t(2, minutes=300, due=ist(3, 17)),  # tomorrow
        t(3, minutes=300, status="done"),
        t(4, minutes=300, status="missed"),
        t(5, minutes=300, status="blocked"),  # cannot be worked on today
        t(6, minutes=300, due=None),  # no deadline: not scheduled for today
    ]
    tasks[5].deadline = None
    assert detect(tasks) == []
    assert kinds(detect(tasks + [t(7, minutes=300, status="in_progress")])) == ["overload"]


def test_the_day_boundary_follows_the_app_timezone_not_utc():
    early = ist(2, 0, 30)  # 00:30 IST is still "today" in India but the previous day in UTC
    late = ist(2, 23, 59)
    assert kinds(detect([t(1, minutes=300, due=early), t(2, minutes=300, due=late)], now=ist(2, 9, 0))) == ["overload"]


def test_tasks_without_an_estimate_count_as_zero_and_are_named():
    [c] = detect([t(1, minutes=600), t(2, minutes=None)])
    assert c.untimed_task_ids == [2] and "1 task(s) due today have no estimate and are counted as 0 min." in c.message


def test_after_hours_or_on_a_day_off_anything_due_today_cannot_fit():
    [after] = detect([t(1, minutes=30, due=ist(2, 23, 59))], now=ist(2, 19, 0))
    assert (after.minutes_needed, after.minutes_available) == (30, 0)
    [weekend] = detect([t(1, minutes=30, due=ist(7, 17))], now=ist(7, 10, 0))
    assert weekend.minutes_available == 0
    assert detect([t(1, minutes=None, due=ist(7, 17))], now=ist(7, 10, 0)) == [], "nothing estimated, nothing to fit"


# ---- what to suggest moving -------------------------------------------------


def test_the_largest_p3_task_is_suggested_first_and_only_as_many_as_needed():
    tasks = [t(1, "P1", 240), t(2, "P2", 180), t(3, "P3", 120), t(4, "P3", 60)]  # 600 needed, 120 over
    [c] = detect(tasks)
    assert [s.task_id for s in c.suggestions] == [3]
    assert c.suggestions[0].estimated_minutes == 120 and c.suggestions[0].priority == "P3"


def test_more_p3_tasks_are_added_until_the_excess_is_covered():
    tasks = [t(1, "P1", 240), t(2, "P2", 180), t(3, "P3", 120), t(4, "P3", 90), t(5, "P3", 30)]  # 660, 180 over
    [c] = detect(tasks)
    assert [s.task_id for s in c.suggestions] == [3, 4]  # 120 + 90 covers 180


def test_p2_tasks_are_suggested_only_when_p3_cannot_free_enough_and_p1_never():
    tasks = [t(1, "P1", 400), t(2, "P2", 200), t(3, "P2", 100), t(4, "P3", 30)]  # 730, 250 over
    [c] = detect(tasks)
    # P3 first (30 min), then the biggest P2 (200 min, 230 so far), still short, so the next P2 (100 min).
    assert [s.task_id for s in c.suggestions] == [4, 2, 3]
    assert all(s.priority != "P1" for s in c.suggestions)
    assert "Even moving" not in c.message, "330 min freed covers the 250 min excess"


def test_if_nothing_movable_is_enough_the_message_says_so():
    [c] = detect([t(1, "P1", 600), t(2, "P3", 30)])
    assert [s.task_id for s in c.suggestions] == [2]
    assert "Even moving every P2 and P3 task would not make it fit." in c.message
    [only_p1] = detect([t(1, "P1", 600)])
    assert only_p1.suggestions == []


def test_suggestions_propose_the_same_time_on_the_next_working_day():
    [c] = detect([t(1, "P1", 400, due=ist(2, 17)), t(2, "P3", 200, due=ist(2, 17))])
    assert c.suggestions[0].proposed_deadline == ist(3, 17)
    friday = ist(6, 10)
    [f] = detect([t(1, "P1", 400, due=ist(6, 17)), t(2, "P3", 200, due=ist(6, 17))], now=friday)
    assert f.suggestions[0].proposed_deadline == ist(9, 17), "Friday moves to Monday, skipping the weekend"


def test_next_working_day_keeps_the_local_time_of_day():
    assert next_working_day_same_time(ist(2, 23, 59), DAYS, IST) == ist(3, 23, 59)
    assert next_working_day_same_time(ist(6, 9, 15), DAYS, IST) == ist(9, 9, 15)
    assert next_working_day_same_time(ist(2, 17), [1], IST) == ist(3, 17)  # only Tuesdays work
    assert next_working_day_same_time(ist(2, 17), [0], IST) == ist(9, 17)  # only Mondays: a week later


# ---- two P1 tasks due at the same time --------------------------------------


def test_two_p1_tasks_with_the_same_deadline_clash():
    [c] = detect([t(1, "P1", 30), t(2, "P1", 90)])
    assert c.kind == "p1_clash" and c.task_ids == [1, 2]
    assert "2 P1 tasks are due at the same time (Mon 02 Nov 17:00)" in c.message
    assert 'Consider doing the shortest first ("task 1", 30 min).' in c.message
    assert c.suggestions == [], "no automatic move for a P1 clash: the person decides"


def test_a_clash_needs_the_same_instant_and_both_to_be_p1():
    assert detect([t(1, "P1", 30), t(2, "P1", 30, due=ist(2, 17) + timedelta(seconds=1))]) == []
    assert detect([t(1, "P1", 30), t(2, "P2", 30)]) == []
    assert detect([t(1, "P2", 30), t(2, "P2", 30)]) == []


def test_clashes_ignore_finished_and_deadlineless_tasks_but_include_blocked_ones():
    base = [t(1, "P1", 30)]
    assert detect(base + [t(2, "P1", 30, status="done")]) == []
    assert detect(base + [t(2, "P1", 30, status="missed")]) == []
    no_deadline = t(2, "P1", 30)
    no_deadline.deadline = None
    assert detect(base + [no_deadline]) == []
    assert kinds(detect(base + [t(2, "P1", 30, status="blocked")])) == ["p1_clash"]


def test_three_way_clash_is_one_conflict_and_separate_deadlines_are_separate_conflicts():
    three = detect([t(1, "P1", 30), t(2, "P1", 20), t(3, "P1", 10)])
    assert len(three) == 1 and three[0].task_ids == [3, 2, 1] and "3 P1 tasks" in three[0].message
    two_groups = detect([t(1, "P1", 30, due=ist(4, 17)), t(2, "P1", 30, due=ist(4, 17)), t(3, "P1", 30, due=ist(3, 17)), t(4, "P1", 30, due=ist(3, 17))])
    assert [c.task_ids for c in two_groups] == [[3, 4], [1, 2]], "earliest deadline first"


def test_both_rules_can_fire_together_with_overload_first():
    tasks = [t(1, "P1", 300), t(2, "P1", 300), t(3, "P3", 100)]
    assert kinds(detect(tasks)) == ["overload", "p1_clash"]
