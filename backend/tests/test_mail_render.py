from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from backend.mail import render
from backend.mail.drafting import DraftOutput, find_recipient, is_org_address
from backend.config import Settings
from backend.directory import load_directory
from backend.models import EmailDraft, Task, User

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 2, 11, 30, tzinfo=UTC)  # 17:00 IST
DIRECTORY = load_directory(Settings(_env_file=None).directory_path)
OWNER = User(id=1, name="Jiya Sharma", email="jiya.sharma@acme.example.com", password_hash="x", team_id="product")


def task(**over) -> Task:
    base = dict(id=1, owner_id=1, title="Send the report", problem_statement="Goal: send it", deadline=datetime(2026, 11, 2, 12, 0, tzinfo=UTC), reviewer_email="ravi.kumar@acme.example.com")
    return Task(**{**base, **over})


# ---- reviewer emails --------------------------------------------------------


def test_completed_on_time():
    subject, text, html = render.reviewer_email(task(completed_at=NOW), OWNER, "completed", NOW, IST)
    assert subject == "[TaskPilot] Completed: Send the report"
    assert "Outcome: Completed on Mon 02 Nov 17:00 IST (before the deadline)." in text
    assert "Deadline: Mon 02 Nov 17:30 IST" in text and "Goal: send it" in text


def test_completed_late_says_so():
    late = datetime(2026, 11, 2, 13, 0, tzinfo=UTC)
    _, text, _ = render.reviewer_email(task(completed_at=late), OWNER, "completed", late, IST)
    assert "Completed late" in text and "the deadline was Mon 02 Nov 17:30 IST" in text


def test_missed_outcome():
    subject, text, _ = render.reviewer_email(task(), OWNER, "missed", NOW, IST)
    assert subject.startswith("[TaskPilot] Missed deadline:") and "Deadline missed: it was due Mon 02 Nov 17:30 IST" in text


def test_the_footer_names_the_person_it_is_sent_on_behalf_of():
    for content in render.reviewer_email(task(completed_at=NOW), OWNER, "completed", NOW, IST)[1:]:
        assert "Sent by TaskPilot on behalf of Jiya Sharma." in content


def test_task_text_is_escaped_in_html_and_never_trusted_in_headers():
    nasty = '<script>alert(1)</script> & "quotes"'
    t = task(title=nasty + "\r\nBcc: evil@x.com", problem_statement="<img src=x onerror=alert(1)>\nline two", completed_at=NOW)
    owner = User(id=1, name="<b>Jiya</b>", email="j@acme.example.com", password_hash="x", team_id="product")
    subject, text, html = render.reviewer_email(t, owner, "completed", NOW, IST)
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<img" not in html and "&lt;img" in html and "<b>Jiya</b>" not in html
    assert "\n" not in subject and "\r" not in subject
    assert "line two" in html and "<br>" in html


def test_no_links_are_added_by_the_template():
    _, text, html = render.reviewer_email(task(completed_at=NOW), OWNER, "completed", NOW, IST)
    assert "http" not in text and "href" not in html


def test_missing_statement_and_deadline_are_handled():
    t = task(problem_statement="", deadline=None, completed_at=NOW)
    _, text, _ = render.reviewer_email(t, OWNER, "completed", NOW, IST)
    assert "(no problem statement)" in text and "Deadline: none" in text and "Completed on Mon 02 Nov 17:00 IST." in text


# ---- approved drafts --------------------------------------------------------


def test_draft_email_is_exactly_what_was_approved_plus_the_footer():
    draft = EmailDraft(task_id=1, subject="Weekly report\r\nBcc: x@y.z", body="Hi Ravi,\n<b>Report</b> attached soon.\nThanks,\nJiya")
    subject, text, html = render.draft_email(draft, OWNER)
    assert "\n" not in subject
    assert text.startswith("Hi Ravi,\n<b>Report</b> attached soon.\nThanks,\nJiya\n\n--\nSent by TaskPilot on behalf of Jiya Sharma.")
    assert "&lt;b&gt;Report&lt;/b&gt;" in html and "<b>Report</b>" not in html


# ---- recipient matching: code decides, never the model ---------------------


def test_recipient_from_a_first_name_a_full_name_or_a_possessive():
    assert find_recipient(task(title="send the weekly report to Ravi by 5"), DIRECTORY, OWNER).name == "Ravi Kumar"
    assert find_recipient(task(title="email meera nair the invoice"), DIRECTORY, OWNER).name == "Meera Nair"
    assert find_recipient(task(title="forward this to Karan's inbox"), DIRECTORY, OWNER).name == "Karan Mehta"


def test_no_recipient_when_nobody_or_several_people_are_named():
    assert find_recipient(task(title="send the release notes to the whole product team"), DIRECTORY, OWNER) is None
    assert find_recipient(task(title="send this to Ravi and Anita"), DIRECTORY, OWNER) is None
    assert find_recipient(task(title="send it to him asap", problem_statement=""), DIRECTORY, OWNER) is None


def test_the_reviewer_named_in_the_ai_statement_is_not_mistaken_for_the_recipient():
    # Found live: "sent to Ravi after Meera Nair reviews it" used to match two people and give up.
    t = task(
        title="Email weekly report to Ravi",
        problem_statement="Goal: Send the weekly report email to Ravi\nDone when: Email is sent to Ravi after Meera Nair reviews it",
        reviewer_email="meera.nair@acme.example.com",
    )
    assert find_recipient(t, DIRECTORY, OWNER).name == "Ravi Kumar"


def test_the_persons_own_words_beat_the_ai_statement():
    t = task(title="send it to Meera", problem_statement="Goal: tell Ravi", reviewer_email="meera.nair@acme.example.com")
    assert find_recipient(t, DIRECTORY, OWNER).name == "Meera Nair", "named in the title, even though she is also the reviewer"


def test_the_statement_is_only_a_fallback_and_the_reviewer_is_ignored_there():
    t = task(title="send the weekly report", problem_statement="Goal: send it to Ravi, Meera Nair will review", reviewer_email="meera.nair@acme.example.com")
    assert find_recipient(t, DIRECTORY, OWNER).name == "Ravi Kumar"
    still_two = task(title="send the weekly report", problem_statement="Goal: send it to Ravi or Anita", reviewer_email=None)
    assert find_recipient(still_two, DIRECTORY, OWNER) is None


def test_the_sender_is_never_picked_as_the_recipient():
    assert find_recipient(task(title="note to self: Jiya should tidy up", problem_statement=""), DIRECTORY, OWNER) is None


def test_a_name_inside_another_word_does_not_match():
    assert find_recipient(task(title="send the raviolis recipe", problem_statement=""), DIRECTORY, OWNER) is None


# ---- links and addresses are never allowed in a model draft ----------------


def test_drafts_reject_links_and_addresses():
    import pytest

    for bad in ("see https://evil.example/x", "visit www.evil.com", "check evil.io", "mail me at a@b.com"):
        with pytest.raises(ValueError, match="links, URLs or email addresses"):
            DraftOutput(subject="Hello", body=bad)
        with pytest.raises(ValueError):
            DraftOutput(subject=bad, body="Hello")
    assert DraftOutput(subject="Weekly report", body="Hi Ravi,\nHere is v2.0 of the report.\nThanks,\nJiya").subject == "Weekly report"


def test_org_domain_check_is_exact():
    assert is_org_address("ravi.kumar@acme.example.com", "acme.example.com")
    assert is_org_address("RAVI@ACME.EXAMPLE.COM", "acme.example.com")
    for outside in ("x@evilacme.example.com", "x@acme.example.com.evil.io", "x@example.com", "not-an-address", "a@b@acme.example.com.evil"):
        assert not is_org_address(outside, "acme.example.com"), outside
