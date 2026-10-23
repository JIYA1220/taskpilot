from datetime import UTC, datetime

import pytest
from sqlmodel import select

from backend.api.deps import get_graph
from backend.config import Settings
from backend.directory import load_directory
from backend.llm import GroqLLM
from backend.main import app
from backend.models import ClarifyingQuestion, Task
from backend.organize.graph import GraphDeps, build_graph
from backend.organize.runtime import make_checkpointer
from backend.tests.conftest import DEMO_PASSWORD
from backend.tests.fakes import FIXED_NOW, REFUND, REPORT, FakeTransport, extract_reply, rewrite_reply

JIYA = "jiya.sharma@acme.example.com"
MEERA = "meera.nair@acme.example.com"


@pytest.fixture
def fake():
    return FakeTransport(
        extract=[extract_reply(REFUND, REPORT)],
        rewrite=[rewrite_reply({"priority": "P1", "priority_reason": "Blocks others."}, {"priority": "P2"})],
    )


@pytest.fixture
def api(client, seeded, settings, fake):
    deps = GraphDeps(GroqLLM(fake), settings, load_directory(settings.directory_path), lambda: FIXED_NOW)
    graph = build_graph(deps, make_checkpointer(":memory:"))
    app.dependency_overrides[get_graph] = lambda: graph
    return client


def auth(client, email=JIYA):
    token = client.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_organize_requires_login(api):
    assert api.post("/api/organize", json={"text": "x"}).status_code == 401
    assert api.post("/api/organize/1:a/answers", json={"answers": {}}).status_code == 401
    assert api.get("/api/tasks").status_code == 401
    assert api.post("/api/tasks", json={"tasks": []}).status_code == 401


def test_full_flow_organize_answer_edit_save_list(api, session):
    h = auth(api)
    first = api.post("/api/organize", json={"text": "ask payments team for the refund API\nsend weekly report to Ravi by 5"}, headers=h).json()
    assert first["status"] == "needs_answers" and 1 <= len(first["questions"]) <= 3
    assert first["thread_id"].startswith("1:")

    answers = {q["id"]: ("Ravi Kumar" if q["field"] == "reviewer" else "P1") for q in first["questions"]}
    done = api.post(f"/api/organize/{first['thread_id']}/answers", json={"answers": answers}, headers=h).json()
    assert done["status"] == "ready" and len(done["tasks"]) == 2
    proposed = done["tasks"]
    assert proposed[0]["priority"] == "P1" and proposed[0]["problem_statement"].startswith("Goal:")
    assert datetime.fromisoformat(proposed[1]["deadline"]) == datetime(2026, 11, 2, 11, 30, tzinfo=UTC)  # 17:00 IST
    assert proposed[1]["reviewer_email"] == "ravi.kumar@acme.example.com"

    proposed[0]["title"] = "Get the refund API from Payments"  # the person edits before saving
    saved = api.post("/api/tasks", json={"tasks": proposed}, headers=h)
    assert saved.status_code == 201 and len(saved.json()) == 2

    listed = api.get("/api/tasks", headers=h).json()
    assert [t["title"] for t in listed] == ["Get the refund API from Payments", "Send weekly report to Ravi"]
    assert listed[0]["dependency_hint"]["team"] == "payments"
    assert session.exec(select(ClarifyingQuestion)).all(), "questions and answers are stored with the task"


def test_questions_can_all_be_skipped_over_the_api(api):
    h = auth(api)
    first = api.post("/api/organize", json={"text": "x"}, headers=h).json()
    done = api.post(f"/api/organize/{first['thread_id']}/answers", json={"answers": {}}, headers=h)
    assert done.status_code == 200 and done.json()["status"] == "ready"


def test_one_user_cannot_resume_anothers_session(api):
    thread = api.post("/api/organize", json={"text": "x"}, headers=auth(api)).json()["thread_id"]
    other = api.post(f"/api/organize/{thread}/answers", json={"answers": {}}, headers=auth(api, MEERA))
    assert other.status_code == 404


def test_answering_twice_is_a_conflict(api):
    h = auth(api)
    thread = api.post("/api/organize", json={"text": "x"}, headers=h).json()["thread_id"]
    assert api.post(f"/api/organize/{thread}/answers", json={"answers": {}}, headers=h).status_code == 200
    assert api.post(f"/api/organize/{thread}/answers", json={"answers": {}}, headers=h).status_code == 409


@pytest.mark.parametrize("text", ["", "   ", "x" * 4001])
def test_bad_organize_text_is_rejected(api, text):
    assert api.post("/api/organize", json={"text": text}, headers=auth(api)).status_code == 422


def test_tasks_are_private_to_their_owner(api):
    mine = {"title": "Mine"}
    assert api.post("/api/tasks", json={"tasks": [mine]}, headers=auth(api)).status_code == 201
    assert api.get("/api/tasks", headers=auth(api, MEERA)).json() == []
    assert len(api.get("/api/tasks", headers=auth(api)).json()) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"title": ""},
        {"title": "t", "priority": "P9"},
        {"title": "t", "deadline": "2026-11-02T17:00:00"},
        {"title": "t", "reviewer_email": "not-an-email"},
        {"title": "t", "estimated_minutes": 0},
        {"title": "t", "dependency_hint": {"direction": "need_from_them", "team": "marketing", "what_is_needed": "x"}},
    ],
)
def test_invalid_tasks_are_rejected_on_save(api, bad):
    assert api.post("/api/tasks", json={"tasks": [bad]}, headers=auth(api)).status_code == 422


def test_manual_review_flag_and_default_priority_are_saved(api, session):
    body = {"tasks": [{"title": "Check this by hand", "needs_manual_review": True}]}
    out = api.post("/api/tasks", json=body, headers=auth(api)).json()[0]
    assert out["needs_manual_review"] is True and out["priority"] == "P2" and out["status"] == "todo"
    assert session.exec(select(Task)).one().owner_id == 1


def test_audit_is_not_required_for_the_api_to_work_without_a_db_sink(api):
    # The fake graph has no audit sink; the endpoint must not depend on one.
    assert api.post("/api/organize", json={"text": "x"}, headers=auth(api)).status_code == 200
