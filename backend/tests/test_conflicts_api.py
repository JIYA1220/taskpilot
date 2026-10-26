"""Dependencies and schedule conflicts through the HTTP API."""

from datetime import UTC, datetime

import pytest
from sqlmodel import select

from backend.api.deps import get_clock, get_llm
from backend.llm import GroqLLM
from backend.main import app
from backend.models import Notification
from backend.tests.conftest import DEMO_PASSWORD
from backend.tests.fakes import FakeTransport

JIYA, MEERA = "jiya.sharma@acme.example.com", "meera.nair@acme.example.com"
RAVI = "ravi.kumar@acme.example.com"
MON_10_IST = datetime(2026, 11, 2, 4, 30, tzinfo=UTC)
FIVE_PM = "2026-11-02T11:30:00+00:00"  # 17:00 IST on Monday 2 Nov


@pytest.fixture
def transport():
    return FakeTransport(match=[{"team": "data", "reason": "owns dashboards"}])


@pytest.fixture
def api(client, seeded, transport):
    app.dependency_overrides[get_llm] = lambda: GroqLLM(transport)
    app.dependency_overrides[get_clock] = lambda: (lambda: MON_10_IST)
    return client


def auth(client, email=JIYA):
    token = client.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def hint(direction="need_from_them", team="payments", what="refund API access"):
    return {"direction": direction, "team": team, "what_is_needed": what}


def save(api, h, title="ask the payments team for the refund api", **fields):
    body = {"title": title, "source_text": title, **fields}
    response = api.post("/api/tasks", json={"tasks": [body]}, headers=h)
    assert response.status_code == 201, response.text
    return response.json()[0]


def deps(api, h, state="all"):
    return api.get(f"/api/dependencies?state={state}", headers=h).json()


def notification_kinds(session):
    return sorted(n.kind for n in session.exec(select(Notification)).all())


# ---- access -----------------------------------------------------------------


def test_everything_requires_login(api):
    for method, url in [
        ("get", "/api/dependencies"), ("post", "/api/tasks/1/dependencies"), ("put", "/api/dependencies/1"),
        ("post", "/api/dependencies/1/resolve"), ("get", "/api/conflicts"), ("post", "/api/tasks/1/deadline"),
    ]:
        assert getattr(api, method)(url).status_code in (401, 422), url
        assert getattr(api, method)(url).status_code != 200


# ---- saving a task that depends on another team ----------------------------


def test_saving_a_task_that_needs_another_team_blocks_it_and_tells_the_reviewer(api, sender, seeded):
    h = auth(api)
    task = save(api, h, dependency_hint=hint(), reviewer_email=RAVI)
    assert task["status"] == "blocked"
    [dep] = deps(api, h)
    assert (dep["team_name"], dep["direction"], dep["match_method"], dep["hint_team"], dep["state"]) == ("Payments", "need_from_them", "keyword", "payments", "open")
    assert dep["what_is_needed"] == "refund API access"
    [mail] = sender.sent
    assert mail.to == RAVI and mail.subject.startswith("[TaskPilot] Blocked:") and "waiting on Payments for refund API access" in mail.text
    assert "blocked" in notification_kinds(seeded)


def test_something_another_team_needs_from_me_is_recorded_without_blocking(api, sender):
    h = auth(api)
    task = save(api, h, "Anita needs the KPI numbers from me", dependency_hint=hint("they_need_from_me", "data", "the KPI numbers"), reviewer_email=RAVI)
    assert task["status"] == "todo" and sender.sent == []
    [dep] = deps(api, h)
    assert (dep["direction"], dep["depends_on_team_id"]) == ("they_need_from_me", "data")


def test_no_dependency_hint_means_no_dependency(api):
    h = auth(api)
    assert save(api, h, "fix the login bug")["status"] == "todo"
    assert save(api, h, "tidy up", dependency_hint=hint("none", "none", ""))["status"] == "todo"
    assert deps(api, h) == []


def test_when_keywords_are_unclear_the_fast_model_decides_and_it_is_recorded(api, transport):
    h = auth(api)
    save(api, h, "need the dashboard fixed", dependency_hint=hint(team="unknown", what="dashboard fix"))
    [dep] = deps(api, h)
    assert (dep["depends_on_team_id"], dep["team_name"], dep["match_method"]) == ("data", "Data and Analytics", "llm")
    assert transport.count("match") == 1


# ---- adding one the AI missed ------------------------------------------------


def test_a_person_can_flag_a_dependency_the_ai_missed(api, sender):
    h = auth(api)
    task = save(api, h, "Prepare the demo", reviewer_email=RAVI)
    created = api.post(f"/api/tasks/{task['id']}/dependencies", json={"direction": "need_from_them", "what_is_needed": "staging access", "team_id": "platform"}, headers=h)
    assert created.status_code == 201 and (created.json()["match_method"], created.json()["team_name"]) == ("manual", "Platform Engineering")
    assert api.get("/api/tasks", headers=h).json()[0]["status"] == "blocked"
    assert len(sender.sent) == 1


def test_a_flagged_dependency_without_a_team_is_matched_for_the_person(api):
    h = auth(api)
    task = save(api, h, "Prepare the demo")
    created = api.post(f"/api/tasks/{task['id']}/dependencies", json={"direction": "need_from_them", "what_is_needed": "access to the staging environment"}, headers=h).json()
    assert (created["depends_on_team_id"], created["match_method"]) == ("platform", "keyword")


@pytest.mark.parametrize(
    "body",
    [
        {"direction": "sideways", "what_is_needed": "x"},
        {"direction": "need_from_them", "what_is_needed": ""},
        {"direction": "need_from_them", "what_is_needed": "x", "team_id": "marketing"},
        {"direction": "need_from_them", "what_is_needed": "x", "team_id": "product"},  # my own team
    ],
)
def test_invalid_flagged_dependencies_are_rejected(api, body):
    h = auth(api)
    task = save(api, h, "Prepare the demo")
    assert api.post(f"/api/tasks/{task['id']}/dependencies", json=body, headers=h).status_code == 422


def test_only_open_tasks_of_my_own_can_get_a_dependency(api):
    h = auth(api)
    task = save(api, h, "Prepare the demo")
    api.post(f"/api/tasks/{task['id']}/status", json={"status": "done"}, headers=h)
    body = {"direction": "need_from_them", "what_is_needed": "x", "team_id": "platform"}
    assert api.post(f"/api/tasks/{task['id']}/dependencies", json=body, headers=h).status_code == 409
    assert api.post(f"/api/tasks/{task['id']}/dependencies", json=body, headers=auth(api, MEERA)).status_code == 404


# ---- follow-up answers ------------------------------------------------------


def blocked_dep(api, h):
    save(api, h, dependency_hint=hint())
    return deps(api, h)[0]


def test_follow_up_answers_are_saved(api):
    h = auth(api)
    dep = blocked_dep(api, h)
    answers = {"what_is_needed": "Sandbox key for the refund API", "needed_by": "2026-11-04T11:30:00+00:00", "already_asked": "Meera, on Slack", "team_id": "payments"}
    updated = api.put(f"/api/dependencies/{dep['id']}", json=answers, headers=h).json()
    assert updated["what_is_needed"] == "Sandbox key for the refund API" and updated["already_asked"] == "Meera, on Slack"
    assert updated["needed_by"].startswith("2026-11-04T11:30:00") and updated["match_method"] == dep["match_method"], "same team: method unchanged"


def test_choosing_a_different_team_marks_the_match_as_manual(api):
    h = auth(api)
    dep = blocked_dep(api, h)
    updated = api.put(f"/api/dependencies/{dep['id']}", json={"what_is_needed": "x", "team_id": "platform"}, headers=h).json()
    assert (updated["depends_on_team_id"], updated["match_method"], updated["hint_team"]) == ("platform", "manual", "payments")


@pytest.mark.parametrize(
    "bad",
    [
        {"what_is_needed": ""},
        {"what_is_needed": "x", "needed_by": "2026-11-04T10:00:00"},  # no UTC offset
        {"what_is_needed": "x", "team_id": "marketing"},
        {"what_is_needed": "x", "team_id": "product"},
        {"what_is_needed": "x", "already_asked": "y" * 301},
    ],
)
def test_invalid_follow_ups_are_rejected(api, bad):
    h = auth(api)
    dep = blocked_dep(api, h)
    assert api.put(f"/api/dependencies/{dep['id']}", json=bad, headers=h).status_code == 422


def test_other_users_cannot_see_or_change_my_dependencies(api):
    h = auth(api)
    dep = blocked_dep(api, h)
    other = auth(api, MEERA)
    assert deps(api, other) == []
    assert api.put(f"/api/dependencies/{dep['id']}", json={"what_is_needed": "x"}, headers=other).status_code == 404
    assert api.post(f"/api/dependencies/{dep['id']}/resolve", headers=other).status_code == 404


# ---- resolving --------------------------------------------------------------


def test_resolving_unblocks_the_task_and_lists_move_to_resolved(api, seeded):
    h = auth(api)
    dep = blocked_dep(api, h)
    resolved = api.post(f"/api/dependencies/{dep['id']}/resolve", headers=h)
    assert resolved.status_code == 200 and resolved.json()["state"] == "resolved" and resolved.json()["resolved_at"]
    assert api.get("/api/tasks", headers=h).json()[0]["status"] == "todo"
    assert deps(api, h, "open") == [] and len(deps(api, h, "resolved")) == 1
    assert "unblocked" in notification_kinds(seeded)
    assert api.post(f"/api/dependencies/{dep['id']}/resolve", headers=h).status_code == 409
    assert api.put(f"/api/dependencies/{dep['id']}", json={"what_is_needed": "x"}, headers=h).status_code == 409


def test_a_blocked_task_cannot_be_started_by_hand_but_can_be_finished(api):
    h = auth(api)
    task = save(api, h, dependency_hint=hint())
    assert api.post(f"/api/tasks/{task['id']}/status", json={"status": "in_progress"}, headers=h).status_code == 409
    assert api.post(f"/api/tasks/{task['id']}/status", json={"status": "done"}, headers=h).json()["status"] == "done"


# ---- schedule conflicts through the API ------------------------------------


def test_no_conflicts_when_there_is_nothing_to_report(api):
    h = auth(api)
    save(api, h, "easy", estimated_minutes=30, deadline=FIVE_PM)
    assert api.get("/api/conflicts", headers=h).json() == []


def test_two_p1_tasks_due_together_are_reported(api):
    h = auth(api)
    save(api, h, "Report A", priority="P1", estimated_minutes=60, deadline=FIVE_PM)
    save(api, h, "Report B", priority="P1", estimated_minutes=30, deadline=FIVE_PM)
    [conflict] = api.get("/api/conflicts", headers=h).json()
    assert conflict["kind"] == "p1_clash" and len(conflict["task_ids"]) == 2 and "Report B" in conflict["message"]


def test_overload_suggests_a_p3_task_and_the_person_can_accept_by_moving_it(api):
    h = auth(api)
    save(api, h, "Big P1", priority="P1", estimated_minutes=380, deadline=FIVE_PM)
    save(api, h, "Medium P2", priority="P2", estimated_minutes=100, deadline=FIVE_PM)
    nice = save(api, h, "Nice to have", priority="P3", estimated_minutes=60, deadline=FIVE_PM)  # 540 needed, 480 left
    [conflict] = api.get("/api/conflicts", headers=h).json()
    assert (conflict["kind"], conflict["minutes_needed"], conflict["minutes_available"]) == ("overload", 540, 480)
    [suggestion] = conflict["suggestions"]
    assert (suggestion["task_id"], suggestion["title"], suggestion["priority"]) == (nice["id"], "Nice to have", "P3")
    assert suggestion["proposed_deadline"].startswith("2026-11-03T17:00:00+05:30")

    moved = api.post(f"/api/tasks/{nice['id']}/deadline", json={"deadline": suggestion["proposed_deadline"]}, headers=h)
    assert moved.status_code == 200 and moved.json()["deadline"].startswith("2026-11-03T11:30:00")
    assert api.get("/api/conflicts", headers=h).json() == [], "the person decided, and the conflict is gone"


def test_suggestions_are_never_applied_automatically(api):
    h = auth(api)
    save(api, h, "Big P1", priority="P1", estimated_minutes=450, deadline=FIVE_PM)
    low = save(api, h, "Low", priority="P3", estimated_minutes=60, deadline=FIVE_PM)
    api.get("/api/conflicts", headers=h)
    again = [t for t in api.get("/api/tasks", headers=h).json() if t["id"] == low["id"]][0]
    assert again["deadline"].startswith("2026-11-02T11:30:00")


def test_conflicts_are_private_to_each_user(api):
    save(api, auth(api), "A", priority="P1", deadline=FIVE_PM)
    save(api, auth(api), "B", priority="P1", deadline=FIVE_PM)
    assert api.get("/api/conflicts", headers=auth(api, MEERA)).json() == []


# ---- moving a deadline ------------------------------------------------------


@pytest.mark.parametrize(
    "body,code",
    [
        ({"deadline": "2026-11-02T04:00:00+00:00"}, 422),  # in the past
        ({"deadline": "2026-11-05T10:00:00"}, 422),  # no UTC offset
        ({"deadline": "tomorrow"}, 422),
    ],
)
def test_bad_deadlines_are_rejected(api, body, code):
    h = auth(api)
    task = save(api, h, "x", deadline=FIVE_PM)
    assert api.post(f"/api/tasks/{task['id']}/deadline", json=body, headers=h).status_code == code


def test_only_my_open_tasks_can_be_rescheduled(api):
    h = auth(api)
    task = save(api, h, "x", deadline=FIVE_PM)
    good = {"deadline": "2026-11-05T11:30:00+00:00"}
    assert api.post(f"/api/tasks/{task['id']}/deadline", json=good, headers=auth(api, MEERA)).status_code == 404
    api.post(f"/api/tasks/{task['id']}/status", json={"status": "done"}, headers=h)
    assert api.post(f"/api/tasks/{task['id']}/deadline", json=good, headers=h).status_code == 409
