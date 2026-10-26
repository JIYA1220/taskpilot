"""Blocking, unblocking and telling the reviewer."""

from datetime import timedelta

import pytest
from sqlmodel import select

from backend.config import Settings
from backend.conflicts.dependency import open_blockers, register_dependency, resolve_dependency
from backend.directory import load_directory
from backend.llm import GroqLLM
from backend.mail.outbox import process_outbox
from backend.models import Dependency, EmailEvent, Notification, Task, User
from backend.reminders import build_reminder
from backend.tests.fakes import FakeSender, FakeTransport
from backend.tests.test_reminders import IST, ist

SETTINGS = Settings(_env_file=None, app_env="test")
DIRECTORY = load_directory(SETTINGS.directory_path)
NOW = ist(2, 10)
OWNER = 1  # Jiya, team product
RAVI = "ravi.kumar@acme.example.com"


def mk(session, title="Get the refund API", reviewer=RAVI, **kw) -> Task:
    kw.setdefault("raw_text", title)
    task = Task(owner_id=OWNER, title=title, reviewer_email=reviewer, **kw)
    session.add(task)
    session.commit()
    return task


def register(session, task, direction="need_from_them", what="refund API access", fake=None, **kw):
    llm = GroqLLM(fake or FakeTransport(match=[{"team": "unknown", "reason": "x"}]))
    owner = session.get(User, task.owner_id)
    result = register_dependency(
        session, task, owner, direction=direction, what=what, directory=DIRECTORY, now=NOW, tz=IST, llm=llm, settings=SETTINGS, **kw
    )
    session.commit()
    return result


def notes(session, kind, user=OWNER):
    return session.exec(select(Notification).where(Notification.kind == kind, Notification.user_id == user)).all()


# ---- blocking ---------------------------------------------------------------


def test_needing_something_from_another_team_blocks_the_task_and_records_why(seeded):
    task = mk(seeded, deadline=ist(3, 17))
    dep, event = register(seeded, task, hint_team="payments")
    seeded.refresh(task)
    assert task.status == "blocked"
    assert (dep.depends_on_team_id, dep.direction, dep.state, dep.match_method) == ("payments", "need_from_them", "open", "keyword")
    assert dep.what_is_needed == "refund API access" and dep.hint_team == "payments"
    assert dep.needed_by == task.deadline and set(dep.match_candidates) <= {"payments", "platform", "data"}
    [note] = notes(seeded, "blocked")
    assert 'is blocked: waiting on Payments for refund API access' in note.message and note.payload["dependency_id"] == dep.id


def test_the_reviewer_is_emailed_that_it_is_blocked_and_why(seeded):
    task = mk(seeded)
    _, event = register(seeded, task)
    assert event is not None and (event.kind, event.recipient) == ("blocked", RAVI)
    sender = FakeSender()
    process_outbox(seeded, NOW, sender, IST)
    [mail] = sender.sent
    assert mail.subject == "[TaskPilot] Blocked: Get the refund API"
    assert "Outcome: Blocked: waiting on Payments for refund API access." in mail.text
    assert "Sent by TaskPilot on behalf of Jiya Sharma." in mail.text


def test_a_reviewer_who_is_a_user_also_gets_an_in_app_notice(seeded):
    task = mk(seeded, reviewer="meera.nair@acme.example.com")
    register(seeded, task)
    [note] = notes(seeded, "review_blocked", user=3)
    assert "is blocked: waiting on Payments" in note.message


def test_no_reviewer_means_no_email(seeded):
    _, event = register(seeded, mk(seeded, reviewer=None))
    assert event is None and seeded.exec(select(EmailEvent)).all() == []


def test_the_reviewer_hears_about_a_block_once_even_with_a_second_dependency(seeded):
    task = mk(seeded)
    register(seeded, task)
    _, second = register(seeded, task, what="staging access", fake=FakeTransport(match=[{"team": "platform", "reason": "x"}]))
    assert second is None and len(seeded.exec(select(EmailEvent)).all()) == 1


def test_something_another_team_needs_from_me_is_recorded_but_does_not_block(seeded):
    task = mk(seeded, title="Anita needs the KPI numbers from me")
    dep, event = register(seeded, task, direction="they_need_from_me", what="the KPI numbers")
    seeded.refresh(task)
    assert task.status == "todo" and event is None and dep.depends_on_team_id == "data"
    [note] = notes(seeded, "dependency")
    assert "Data and Analytics is waiting on you" in note.message and notes(seeded, "blocked") == []


def test_an_unidentified_team_still_blocks_the_task(seeded):
    task = mk(seeded, title="Get approval from legal", raw_text=None)
    dep, _ = register(seeded, task, what="contract approval")
    seeded.refresh(task)
    # The model was asked and said "unknown": that is a decision by the model, not a failure.
    assert dep.depends_on_team_id is None and dep.match_method == "llm" and task.status == "blocked"
    failed, _ = register(
        seeded, mk(seeded, "Get approval from finance", reviewer=None), what="budget approval", fake=FakeTransport(match=["junk", "junk"])
    )
    assert failed.depends_on_team_id is None and failed.match_method == "none"
    assert "waiting on another team for contract approval" in notes(seeded, "blocked")[0].message


def test_a_person_choosing_the_team_skips_matching(seeded):
    task = mk(seeded, title="Get approval from legal")
    dep, _ = register_dependency(
        seeded, task, seeded.get(User, OWNER), direction="need_from_them", what="sign-off", directory=DIRECTORY,
        now=NOW, tz=IST, team_id="platform",
    )
    assert (dep.depends_on_team_id, dep.match_method) == ("platform", "manual")


def test_the_extract_steps_pick_is_kept_for_comparison_but_none_is_not(seeded):
    dep, _ = register(seeded, mk(seeded, "Ask platform for staging"), what="staging access", hint_team="payments")
    assert dep.hint_team == "payments" and dep.depends_on_team_id == "platform", "keywords beat the hint"
    dep2, _ = register(seeded, mk(seeded, "Ask payments for the refund API", reviewer=None), hint_team="none")
    assert dep2.hint_team is None


@pytest.mark.parametrize("status,expected", [("todo", "blocked"), ("in_progress", "blocked"), ("done", "done"), ("missed", "missed")])
def test_only_open_tasks_become_blocked(seeded, status, expected):
    task = mk(seeded, status=status)
    register(seeded, task)
    seeded.refresh(task)
    assert task.status == expected


# ---- unblocking -------------------------------------------------------------


def resolve(session, dep, task, now=NOW):
    ok = resolve_dependency(session, dep, task, session.get(User, OWNER), DIRECTORY, now)
    session.commit()
    return ok


def test_resolving_the_only_blocker_unblocks_the_task(seeded):
    task = mk(seeded)
    dep, _ = register(seeded, task)
    later = NOW + timedelta(hours=2)
    assert resolve(seeded, dep, task, later) is True
    seeded.refresh(task), seeded.refresh(dep)
    assert (task.status, dep.state, dep.resolved_at) == ("todo", "resolved", later)
    [note] = notes(seeded, "unblocked")
    assert "is unblocked: Payments has delivered refund API access" in note.message


def test_a_task_with_two_blockers_stays_blocked_until_both_are_resolved(seeded):
    task = mk(seeded)
    first, _ = register(seeded, task)
    second, _ = register(seeded, task, what="staging access", fake=FakeTransport(match=[{"team": "platform", "reason": "x"}]))
    assert len(open_blockers(seeded, task.id)) == 2
    resolve(seeded, first, task)
    seeded.refresh(task)
    assert task.status == "blocked" and notes(seeded, "unblocked") == []
    resolve(seeded, second, task)
    seeded.refresh(task)
    assert task.status == "todo" and len(notes(seeded, "unblocked")) == 1


def test_resolving_twice_does_nothing_the_second_time(seeded):
    task = mk(seeded)
    dep, _ = register(seeded, task)
    assert resolve(seeded, dep, task) is True and resolve(seeded, dep, task) is False
    assert len(notes(seeded, "unblocked")) == 1


def test_resolving_never_reopens_a_finished_task_or_unblocks_for_a_reverse_dependency(seeded):
    task = mk(seeded)
    dep, _ = register(seeded, task)
    task.status = "done"
    seeded.add(task)
    seeded.commit()
    resolve(seeded, dep, task)
    seeded.refresh(task)
    assert task.status == "done"
    other = mk(seeded, "Anita needs numbers", reviewer=None)
    reverse, _ = register(seeded, other, direction="they_need_from_me", what="numbers")
    resolve(seeded, reverse, other)
    seeded.refresh(other)
    assert other.status == "todo"


# ---- reminders mention blocked tasks ---------------------------------------


def test_reminders_say_when_a_task_is_blocked(seeded):
    blocked = mk(seeded, "Waiting on payments", reviewer=None, status="blocked", priority="P1")
    plain = mk(seeded, "Free to do", reviewer=None, priority="P2")
    text = build_reminder(OWNER, [blocked, plain], NOW).message
    assert "- P1 Waiting on payments (blocked; no deadline)" in text and "- P2 Free to do (no deadline)" in text
