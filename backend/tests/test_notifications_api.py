from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from backend.api.deps import get_clock
from backend.main import app
from backend.models import Notification, Task
from backend.reminders import run_tick
from backend.tests.conftest import DEMO_PASSWORD
from backend.tests.test_reminders import IST, ist

JIYA = "jiya.sharma@acme.example.com"
MEERA = "meera.nair@acme.example.com"


def auth(client, email=JIYA):
    token = client.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def add_task(client, headers, **fields):
    body = {"title": "A task", **fields}
    return client.post("/api/tasks", json={"tasks": [body]}, headers=headers).json()[0]


@pytest.fixture
def frozen(client):
    """Pin the API's clock; returns a setter."""
    holder = {"now": datetime(2026, 11, 2, 4, 0, tzinfo=UTC)}
    app.dependency_overrides[get_clock] = lambda: (lambda: holder["now"])
    return holder


# ---- notifications ----------------------------------------------------------


def seed_notifications(session, user_id=1, n=3):
    base = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)
    for i in range(n):
        session.add(Notification(user_id=user_id, kind="reminder", message=f"m{i}", created_at=base + timedelta(minutes=i)))
    session.commit()


def test_notifications_require_login(client, seeded):
    assert client.get("/api/notifications").status_code == 401
    assert client.get("/api/settings/reminders").status_code == 401
    assert client.post("/api/notifications/read-all").status_code == 401


def test_list_is_newest_first_with_unread_count(client, seeded):
    seed_notifications(seeded)
    body = client.get("/api/notifications", headers=auth(client)).json()
    assert body["unread"] == 3
    assert [n["message"] for n in body["items"]] == ["m2", "m1", "m0"]


def test_mark_one_and_all_read(client, seeded):
    seed_notifications(seeded)
    h = auth(client)
    newest = client.get("/api/notifications", headers=h).json()["items"][0]["id"]
    assert client.post(f"/api/notifications/{newest}/read", headers=h).status_code == 204
    assert client.get("/api/notifications", headers=h).json()["unread"] == 2
    assert client.post("/api/notifications/read-all", headers=h).status_code == 204
    assert client.get("/api/notifications", headers=h).json()["unread"] == 0


def test_notifications_are_private(client, seeded):
    seed_notifications(seeded, user_id=1)
    first_id = seeded.exec(select(Notification.id)).first()
    assert client.get("/api/notifications", headers=auth(client, MEERA)).json() == {"unread": 0, "items": []}
    assert client.post(f"/api/notifications/{first_id}/read", headers=auth(client, MEERA)).status_code == 404
    client.post("/api/notifications/read-all", headers=auth(client, MEERA))
    assert client.get("/api/notifications", headers=auth(client)).json()["unread"] == 3


def test_limit_is_clamped(client, seeded):
    seed_notifications(seeded, n=5)
    assert len(client.get("/api/notifications?limit=2", headers=auth(client)).json()["items"]) == 2
    assert len(client.get("/api/notifications?limit=0", headers=auth(client)).json()["items"]) == 1


# ---- reminder settings ------------------------------------------------------

VALID = {"reminders_enabled": True, "interval_minutes": 1, "work_start": "00:00", "work_end": "23:59", "work_days": [6, 0, 1]}


def test_defaults_then_update_persists(client, seeded):
    h = auth(client)
    default = client.get("/api/settings/reminders", headers=h).json()
    assert default["interval_minutes"] == 60 and default["work_start"] == "09:00" and default["work_days"] == [0, 1, 2, 3, 4]
    assert default["timezone"] == "Asia/Kolkata" and default["last_reminder_at"] is None
    saved = client.put("/api/settings/reminders", json=VALID, headers=h)
    assert saved.status_code == 200 and saved.json()["work_days"] == [0, 1, 6]
    assert client.get("/api/settings/reminders", headers=h).json()["interval_minutes"] == 1


def test_settings_are_per_user(client, seeded):
    client.put("/api/settings/reminders", json=VALID, headers=auth(client))
    assert client.get("/api/settings/reminders", headers=auth(client, MEERA)).json()["interval_minutes"] == 60


@pytest.mark.parametrize(
    "change",
    [
        {"interval_minutes": 0},
        {"interval_minutes": 1441},
        {"work_start": "9:00"},
        {"work_start": "25:00"},
        {"work_start": "09:00", "work_end": "08:00"},  # ends before it starts
        {"work_start": "10:00", "work_end": "10:00"},
        {"work_days": []},
        {"work_days": [1, 1]},
        {"work_days": [7]},
        {"reminders_enabled": "maybe"},
    ],
)
def test_invalid_settings_are_rejected(client, seeded, change):
    assert client.put("/api/settings/reminders", json={**VALID, **change}, headers=auth(client)).status_code == 422


# ---- completing tasks -------------------------------------------------------


def test_status_flow_sets_and_clears_completed_at(client, seeded, frozen):
    h = auth(client)
    t = add_task(client, h)
    assert t["status"] == "todo" and t["completed_at"] is None
    started = client.post(f"/api/tasks/{t['id']}/status", json={"status": "in_progress"}, headers=h).json()
    assert started["status"] == "in_progress"
    done = client.post(f"/api/tasks/{t['id']}/status", json={"status": "done"}, headers=h).json()
    assert done["status"] == "done" and done["completed_at"].startswith("2026-11-02T04:00:00")
    reopened = client.post(f"/api/tasks/{t['id']}/status", json={"status": "todo"}, headers=h).json()
    assert reopened["status"] == "todo" and reopened["completed_at"] is None


@pytest.mark.parametrize("status", ["missed", "blocked", "banana"])
def test_system_statuses_cannot_be_set_by_hand(client, seeded, status):
    h = auth(client)
    t = add_task(client, h)
    assert client.post(f"/api/tasks/{t['id']}/status", json={"status": status}, headers=h).status_code == 422


def test_other_users_tasks_cannot_be_changed(client, seeded):
    t = add_task(client, auth(client))
    assert client.post(f"/api/tasks/{t['id']}/status", json={"status": "done"}, headers=auth(client, MEERA)).status_code == 404
    assert client.post("/api/tasks/999/status", json={"status": "done"}, headers=auth(client)).status_code == 404


def test_a_missed_task_can_only_be_finished_late(client, seeded, session):
    h = auth(client)
    t = add_task(client, h)
    session.get(Task, t["id"]).status = "missed"
    session.commit()
    assert client.post(f"/api/tasks/{t['id']}/status", json={"status": "todo"}, headers=h).status_code == 409
    assert client.post(f"/api/tasks/{t['id']}/status", json={"status": "done"}, headers=h).json()["status"] == "done"


def test_a_blocked_task_cannot_be_started_by_hand(client, seeded, session):
    h = auth(client)
    t = add_task(client, h)
    session.get(Task, t["id"]).status = "blocked"
    session.commit()
    assert client.post(f"/api/tasks/{t['id']}/status", json={"status": "in_progress"}, headers=h).status_code == 409


# ---- end to end through the API ---------------------------------------------


def test_deadline_passes_then_the_api_shows_missed_and_a_notification(client, seeded, session):
    h = auth(client)
    add_task(client, h, title="Send report", priority="P1", deadline="2026-11-02T11:30:00+00:00")  # 17:00 IST
    run_tick(session, ist(2, 16), IST)  # before the deadline: a reminder, not a miss
    assert client.get("/api/tasks", headers=h).json()[0]["status"] == "todo"
    run_tick(session, ist(2, 17, 1), IST)
    assert client.get("/api/tasks", headers=h).json()[0]["status"] == "missed"
    kinds = [n["kind"] for n in client.get("/api/notifications", headers=h).json()["items"]]
    assert sorted(kinds) == ["missed", "reminder"]


def test_completing_a_task_stops_it_being_reminded_about(client, seeded, session, frozen):
    h = auth(client)
    t = add_task(client, h, title="Quick one")
    client.post(f"/api/tasks/{t['id']}/status", json={"status": "done"}, headers=h)
    assert run_tick(session, ist(2, 10), IST).reminders == []
