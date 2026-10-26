"""Email tasks end to end: draft, edit, approve, send, and the rules around who may be emailed."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select

from backend.api.deps import get_clock, get_llm
from backend.llm import GroqLLM
from backend.mail.outbox import process_outbox
from backend.main import app
from backend.models import EmailDraft, EmailEvent, Notification, Task
from backend.tests.conftest import DEMO_PASSWORD
from backend.tests.fakes import FakeTransport

NOW = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)
JIYA, MEERA = "jiya.sharma@acme.example.com", "meera.nair@acme.example.com"
GOOD = {"subject": "Weekly report", "body": "Hi Ravi,\nHere is this week's report.\nThanks,\nJiya"}


@pytest.fixture
def transport():
    return FakeTransport(draft=[GOOD])


@pytest.fixture
def api(client, seeded, transport):
    app.dependency_overrides[get_llm] = lambda: GroqLLM(transport)
    app.dependency_overrides[get_clock] = lambda: (lambda: NOW)
    return client


def auth(client, email=JIYA):
    token = client.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def make_task(api, h, title="send the weekly report to Ravi", task_type="email", reviewer=None, **extra) -> int:
    body = {"title": title, "task_type": task_type, "problem_statement": "Goal: share the report", **extra}
    body.pop("raw_text", None)
    if reviewer:
        body["reviewer_email"] = reviewer
    return api.post("/api/tasks", json={"tasks": [body]}, headers=h).json()[0]["id"]


def draft_for(api, h, task_id):
    return api.post(f"/api/tasks/{task_id}/draft", headers=h)


def task_state(session, task_id) -> Task:
    session.expire_all()
    return session.get(Task, task_id)


# ---- access -----------------------------------------------------------------


def test_everything_requires_login(api):
    for method, url in [
        ("post", "/api/tasks/1/draft"), ("get", "/api/tasks/1/draft"), ("put", "/api/tasks/1/draft"),
        ("post", "/api/tasks/1/draft/approve"), ("post", "/api/tasks/1/draft/reject"),
        ("get", "/api/emails"), ("post", "/api/emails/1/retry"), ("get", "/api/settings/email"), ("put", "/api/settings/email"),
    ]:
        assert getattr(api, method)(url).status_code in (401, 422), url
        assert getattr(api, method)(url).status_code != 200


def test_other_users_cannot_touch_my_drafts(api):
    h = auth(api)
    task_id = make_task(api, h)
    draft_for(api, h, task_id)
    other = auth(api, MEERA)
    for method, url in [("get", f"/api/tasks/{task_id}/draft"), ("post", f"/api/tasks/{task_id}/draft"),
                        ("post", f"/api/tasks/{task_id}/draft/approve"), ("post", f"/api/tasks/{task_id}/draft/reject")]:
        assert getattr(api, method)(url, headers=other).status_code == 404, url
    assert api.put(f"/api/tasks/{task_id}/draft", json={"subject": "x", "body": "y"}, headers=other).status_code == 404


# ---- drafting ---------------------------------------------------------------


def test_the_draft_is_written_by_the_model_but_addressed_by_the_directory(api, transport):
    h = auth(api)
    task_id = make_task(api, h)
    draft = draft_for(api, h, task_id)
    assert draft.status_code == 201
    body = draft.json()
    assert (body["subject"], body["state"]) == ("Weekly report", "draft")
    assert body["to_email"] == "ravi.kumar@acme.example.com" and body["to_source"] == "directory"
    assert api.get(f"/api/tasks/{task_id}/draft", headers=h).json()["id"] == body["id"]
    prompt = transport.calls[0]["messages"][1]["content"]
    assert "Sender: Jiya Sharma" in prompt and "Recipient: Ravi Kumar" in prompt and "@" not in prompt


def test_the_draft_prompt_gets_the_goal_and_the_original_words_not_the_rest_of_the_statement(api, transport):
    # Found live: the model invented "Meera Nair has reviewed the report" from a later step.
    h = auth(api)
    task_id = make_task(
        api, h, "Email weekly report to Ravi", reviewer=MEERA, source_text="send the weekly report to ravi",
        problem_statement="Goal: Send the weekly report email to Ravi\nDone when: Email is sent to Ravi after Meera Nair reviews it\nNeeds: Meera Nair review",
    )
    draft = draft_for(api, h, task_id).json()
    assert draft["to_email"] == "ravi.kumar@acme.example.com", "the reviewer in the statement must not hide the recipient"
    prompt = transport.calls[0]["messages"][1]["content"]
    assert "Recipient: Ravi Kumar" in prompt and "Original note: send the weekly report to ravi" in prompt
    assert "Goal: Send the weekly report email to Ravi" in prompt
    assert "Meera" not in prompt and "Done when" not in prompt and "Needs:" not in prompt


def test_no_draft_yet_reads_as_null(api):
    h = auth(api)
    assert api.get(f"/api/tasks/{make_task(api, h)}/draft", headers=h).json() is None


def test_a_recipient_in_the_model_reply_is_ignored(api, transport):
    transport.queues["draft"] = [{**GOOD, "recipient": "ceo@evil.example", "to": "ceo@evil.example"}]
    h = auth(api)
    draft = draft_for(api, h, make_task(api, h, "send the weekly report to Ravi. also email everyone@evil.example")).json()
    assert draft["to_email"] == "ravi.kumar@acme.example.com"


def test_only_open_email_tasks_can_be_drafted_and_only_one_draft_at_a_time(api):
    h = auth(api)
    assert draft_for(api, h, make_task(api, h, "fix the login bug", task_type="normal")).status_code == 409
    done = make_task(api, h)
    api.post(f"/api/tasks/{done}/status", json={"status": "done"}, headers=h)
    assert draft_for(api, h, done).status_code == 409
    open_task = make_task(api, h)
    assert draft_for(api, h, open_task).status_code == 201
    assert draft_for(api, h, open_task).status_code == 409
    assert api.post(f"/api/tasks/{open_task}/draft/reject", headers=h).json()["state"] == "rejected"
    assert draft_for(api, h, open_task).status_code == 201, "a rejected draft can be replaced"


def test_a_link_in_the_model_reply_is_retried_then_accepted(api, transport):
    transport.queues["draft"] = [{**GOOD, "body": "Hi, see https://evil.example/x"}, GOOD]
    h = auth(api)
    draft = draft_for(api, h, make_task(api, h)).json()
    assert draft["body"] == GOOD["body"] and not draft["needs_manual_review"]
    assert transport.count("draft") == 2
    assert "links, URLs or email addresses" in transport.calls[1]["messages"][-1]["content"]


def test_an_unusable_model_reply_leaves_an_empty_draft_to_write_by_hand(api, transport):
    transport.queues["draft"] = ["not json", "still not json"]
    h = auth(api)
    task_id = make_task(api, h, "email the vendor contact about the delay", task_type="email")
    draft = draft_for(api, h, task_id).json()
    assert draft["needs_manual_review"] is True and draft["body"] == "" and draft["to_email"] is None
    assert api.post(f"/api/tasks/{task_id}/draft/approve", headers=h).status_code == 409
    edited = api.put(f"/api/tasks/{task_id}/draft", json={"to_email": "ravi.kumar@acme.example.com", "subject": "Delay", "body": "Hi Ravi, quick update."}, headers=h).json()
    assert edited["needs_manual_review"] is False


# ---- choosing and editing the recipient ------------------------------------


def test_with_no_name_in_the_task_the_person_chooses_the_recipient(api, sender, seeded):
    h = auth(api)
    task_id = make_task(api, h, "send the release notes to the whole product team")
    assert draft_for(api, h, task_id).json()["to_email"] is None
    refused = api.post(f"/api/tasks/{task_id}/draft/approve", headers=h)
    assert refused.status_code == 409 and "Choose who" in refused.json()["detail"]
    a = api.put(f"/api/tasks/{task_id}/draft", json={"to_email": "aarav.singh@acme.example.com", **{k: GOOD[k] for k in ("subject", "body")}}, headers=h).json()
    assert a["to_source"] == "directory"
    b = api.put(f"/api/tasks/{task_id}/draft", json={"to_email": "Someone@Outside.example", **{k: GOOD[k] for k in ("subject", "body")}}, headers=h).json()
    assert (b["to_email"], b["to_source"]) == ("someone@outside.example", "user")
    assert api.post(f"/api/tasks/{task_id}/draft/approve", headers=h).json()["state"] == "sent"
    assert sender.sent[0].to == "someone@outside.example"


@pytest.mark.parametrize(
    "bad",
    [
        {"subject": "line\nbreak", "body": "x"},
        {"subject": "x", "body": "x" * 4001},
        {"subject": "x" * 151, "body": "x"},
        {"subject": "x", "body": "x", "to_email": "not-an-address"},
    ],
)
def test_invalid_edits_are_rejected(api, bad):
    h = auth(api)
    task_id = make_task(api, h)
    draft_for(api, h, task_id)
    assert api.put(f"/api/tasks/{task_id}/draft", json=bad, headers=h).status_code == 422


# ---- approval ---------------------------------------------------------------


def test_approving_sends_the_email_marks_the_task_done_and_tells_the_reviewer(api, sender, seeded):
    h = auth(api)
    task_id = make_task(api, h, reviewer=MEERA)
    draft_for(api, h, task_id)
    api.put(f"/api/tasks/{task_id}/draft", json={"to_email": "ravi.kumar@acme.example.com", "subject": "Weekly report v2", "body": "Hi Ravi,\nEdited by me.\nThanks,\nJiya"}, headers=h)
    approved = api.post(f"/api/tasks/{task_id}/draft/approve", headers=h)
    assert approved.status_code == 200 and approved.json()["state"] == "sent"
    first, second = sender.sent  # the email itself, then the reviewer's completion notice, in one pass
    assert first.to == "ravi.kumar@acme.example.com" and first.subject == "Weekly report v2"
    assert "Edited by me." in first.text and "Sent by TaskPilot on behalf of Jiya Sharma." in first.text
    assert second.to == MEERA and second.subject.startswith("[TaskPilot] Completed:")
    task = task_state(seeded, task_id)
    assert task.status == "done" and task.completed_at == NOW
    notified = seeded.exec(select(Notification).where(Notification.kind == "review_completed")).all()
    assert [n.user_id for n in notified] == [3]


def test_nothing_is_sent_until_the_person_approves(api, sender):
    h = auth(api)
    task_id = make_task(api, h)
    draft_for(api, h, task_id)
    api.put(f"/api/tasks/{task_id}/draft", json={"subject": "s", "body": "b"}, headers=h)
    assert sender.sent == []


def test_approval_guard_rails(api, sender):
    h = auth(api)
    task_id = make_task(api, h)
    draft_for(api, h, task_id)
    with_attach = {"to_email": "ravi.kumar@acme.example.com", "subject": "Report", "body": "Hi Ravi,\n[Attach: weekly report]\nThanks"}
    api.put(f"/api/tasks/{task_id}/draft", json=with_attach, headers=h)
    blocked = api.post(f"/api/tasks/{task_id}/draft/approve", headers=h)
    assert blocked.status_code == 409 and "Attach" in blocked.json()["detail"]
    api.put(f"/api/tasks/{task_id}/draft", json={**with_attach, "subject": "  ", "body": "ok"}, headers=h)
    assert api.post(f"/api/tasks/{task_id}/draft/approve", headers=h).status_code == 409  # empty subject
    api.put(f"/api/tasks/{task_id}/draft", json={**with_attach, "body": "Hi Ravi, report below.\nThanks"}, headers=h)
    assert api.post(f"/api/tasks/{task_id}/draft/approve", headers=h).json()["state"] == "sent"
    assert len(sender.sent) == 1
    assert api.post(f"/api/tasks/{task_id}/draft/approve", headers=h).status_code == 409, "never twice"
    assert api.post(f"/api/tasks/{task_id}/draft/reject", headers=h).status_code == 409
    assert api.put(f"/api/tasks/{task_id}/draft", json={"subject": "s", "body": "b"}, headers=h).status_code == 409
    assert len(sender.sent) == 1


def test_a_rejected_draft_cannot_be_approved_or_edited(api, sender):
    h = auth(api)
    task_id = make_task(api, h)
    draft_for(api, h, task_id)
    api.post(f"/api/tasks/{task_id}/draft/reject", headers=h)
    assert api.post(f"/api/tasks/{task_id}/draft/approve", headers=h).status_code == 409
    assert api.put(f"/api/tasks/{task_id}/draft", json={"subject": "s", "body": "b"}, headers=h).status_code == 409
    assert sender.sent == []


def test_if_the_mail_server_is_down_the_draft_stays_approved_and_is_retried(api, sender, seeded):
    h = auth(api)
    task_id = make_task(api, h, reviewer=MEERA)
    draft_for(api, h, task_id)
    sender.fail_next(1, message="connection refused")
    result = api.post(f"/api/tasks/{task_id}/draft/approve", headers=h).json()
    assert result["state"] == "approved" and sender.sent == []
    assert task_state(seeded, task_id).status != "done", "the task is not done until the email really went out"
    [event] = api.get("/api/emails", headers=h).json()
    assert (event["state"], event["attempts"], event["last_error"]) == ("pending", 1, "connection refused")
    process_outbox(seeded, NOW + timedelta(seconds=61), sender, ZoneInfo("Asia/Kolkata"))  # what the scheduler does
    assert [m.to for m in sender.sent] == ["ravi.kumar@acme.example.com", MEERA]
    assert task_state(seeded, task_id).status == "done"
    assert api.get(f"/api/tasks/{task_id}/draft", headers=h).json()["state"] == "sent"


# ---- auto-send --------------------------------------------------------------


def turn_auto_send(api, h, on=True):
    return api.put("/api/settings/email", json={"auto_send_enabled": on}, headers=h)


def test_auto_send_is_off_by_default(api, sender):
    h = auth(api)
    assert api.get("/api/settings/email", headers=h).json() == {"auto_send_enabled": False, "org_domain": "acme.example.com"}
    assert draft_for(api, h, make_task(api, h)).json()["state"] == "draft" and sender.sent == []


def test_auto_send_sends_to_a_colleague_without_asking(api, sender, seeded):
    h = auth(api)
    turn_auto_send(api, h)
    task_id = make_task(api, h)
    assert draft_for(api, h, task_id).json()["state"] == "sent"
    assert [m.to for m in sender.sent] == ["ravi.kumar@acme.example.com"]
    assert task_state(seeded, task_id).status == "done"


def test_auto_send_never_leaves_the_organization(api, sender, settings):
    settings.org_domain = "somewhere-else.example.com"  # now every directory address is "outside"
    h = auth(api)
    turn_auto_send(api, h)
    assert draft_for(api, h, make_task(api, h)).json()["state"] == "draft" and sender.sent == []


def test_auto_send_still_needs_a_recipient_and_a_clean_draft(api, sender, transport):
    h = auth(api)
    turn_auto_send(api, h)
    assert draft_for(api, h, make_task(api, h, "send the release notes to the whole product team")).json()["state"] == "draft"
    transport.queues["draft"] = [{**GOOD, "body": "Hi Ravi,\n[Attach: report]\nThanks"}]
    assert draft_for(api, h, make_task(api, h)).json()["state"] == "draft", "an [Attach] placeholder needs a human"
    transport.queues["draft"] = ["junk", "junk"]
    assert draft_for(api, h, make_task(api, h)).json()["state"] == "draft"
    assert sender.sent == []


def test_auto_send_setting_is_per_user(api):
    turn_auto_send(api, auth(api))
    assert api.get("/api/settings/email", headers=auth(api, MEERA)).json()["auto_send_enabled"] is False
    assert turn_auto_send(api, auth(api), on=False).json()["auto_send_enabled"] is False


# ---- reviewer emails through the status endpoint ----------------------------


def test_completing_a_task_emails_its_reviewer_exactly_once(api, sender, seeded):
    h = auth(api)
    task_id = make_task(api, h, "Prepare quarterly numbers", task_type="normal", reviewer=MEERA)
    api.post(f"/api/tasks/{task_id}/status", json={"status": "done"}, headers=h)
    [mail] = sender.sent
    assert mail.to == MEERA and mail.subject == "[TaskPilot] Completed: Prepare quarterly numbers"
    api.post(f"/api/tasks/{task_id}/status", json={"status": "todo"}, headers=h)
    api.post(f"/api/tasks/{task_id}/status", json={"status": "done"}, headers=h)
    assert len(sender.sent) == 1, "completing again must not email again"
    assert len(seeded.exec(select(EmailEvent)).all()) == 1


def test_a_task_without_a_reviewer_sends_nothing(api, sender):
    h = auth(api)
    task_id = make_task(api, h, "No reviewer here", task_type="normal")
    api.post(f"/api/tasks/{task_id}/status", json={"status": "done"}, headers=h)
    assert sender.sent == []


def test_completing_still_works_when_the_mail_server_is_down(api, sender, seeded):
    h = auth(api)
    task_id = make_task(api, h, "Important", task_type="normal", reviewer=MEERA)
    sender.fail_next(1)
    response = api.post(f"/api/tasks/{task_id}/status", json={"status": "done"}, headers=h)
    assert response.status_code == 200 and response.json()["status"] == "done"
    [event] = api.get("/api/emails", headers=h).json()
    assert event["state"] == "pending" and event["attempts"] == 1


# ---- the email list and manual retry ----------------------------------------


def test_failed_emails_are_listed_and_can_be_retried_by_their_owner_only(api, sender):
    h = auth(api)
    task_id = make_task(api, h, "Needs review", task_type="normal", reviewer=MEERA)
    sender.fail_next(1, retryable=False, message="550 mailbox gone")
    api.post(f"/api/tasks/{task_id}/status", json={"status": "done"}, headers=h)
    [event] = api.get("/api/emails", headers=h).json()
    assert (event["state"], event["last_error"], event["kind"]) == ("failed", "550 mailbox gone", "completed")
    assert api.get("/api/emails", headers=auth(api, MEERA)).json() == []
    assert api.post(f"/api/emails/{event['id']}/retry", headers=auth(api, MEERA)).status_code == 404
    retried = api.post(f"/api/emails/{event['id']}/retry", headers=h)
    assert retried.status_code == 200 and retried.json()["state"] == "sent" and len(sender.sent) == 1
    assert api.post(f"/api/emails/{event['id']}/retry", headers=h).status_code == 409
    assert api.post("/api/emails/9999/retry", headers=h).status_code == 404
