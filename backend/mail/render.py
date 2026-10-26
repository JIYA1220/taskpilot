"""Turn tasks and drafts into email content.

Everything that originates from a person or a model (titles, statements, draft text) is
untrusted: it is HTML-escaped in the HTML part, never placed unsanitised in a header, and
this module never adds links of its own."""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from backend.mail.sender import clean_header
from backend.models import EmailDraft, Task, User

MAX_STATEMENT = 2000


def footer(owner_name: str) -> str:
    return f"Sent by TaskPilot on behalf of {clean_header(owner_name, 100)}."


def _when(value: datetime, tz: ZoneInfo) -> str:
    return value.astimezone(tz).strftime("%a %d %b %H:%M %Z")


def _html_page(paragraphs_html: str, owner_name: str) -> str:
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1e293b">'
        f"{paragraphs_html}"
        '<hr style="border:none;border-top:1px solid #e2e8f0;margin:16px 0">'
        f'<p style="color:#64748b;font-size:12px">{escape(footer(owner_name))}</p></div>'
    )


def _html_text(text: str) -> str:
    return "<br>".join(escape(line) for line in text.splitlines())


def outcome_line(task: Task, kind: str, now: datetime, tz: ZoneInfo, detail: str = "") -> str:
    if kind == "blocked":
        return f"Blocked: {clean_header(detail, 300) or 'waiting on another team'}."
    if kind == "missed":
        if task.deadline is None:
            return "Deadline missed and the task is not done yet."
        return f"Deadline missed: it was due {_when(task.deadline, tz)} and is not done yet."
    done_at = task.completed_at or now
    if task.deadline and done_at > task.deadline:
        return f"Completed late, on {_when(done_at, tz)} (the deadline was {_when(task.deadline, tz)})."
    return f"Completed on {_when(done_at, tz)}" + (" (before the deadline)." if task.deadline else ".")


def reviewer_email(task: Task, owner: User, kind: str, now: datetime, tz: ZoneInfo, detail: str = "") -> tuple[str, str, str]:
    """(subject, text, html) for the reviewer of a task that was completed, missed its deadline or got blocked."""
    title = clean_header(task.title, 120)
    label = {"completed": "Completed", "missed": "Missed deadline", "blocked": "Blocked"}[kind]
    subject = f"[TaskPilot] {label}: {title}"
    outcome = outcome_line(task, kind, now, tz, detail)
    deadline = _when(task.deadline, tz) if task.deadline else "none"
    statement = (task.problem_statement or "(no problem statement)")[:MAX_STATEMENT]
    owner_name = clean_header(owner.name, 100)
    text = (
        f"Hello,\n\n{owner_name} has a task you are set to review.\n\n"
        f"Task: {task.title}\nOutcome: {outcome}\nDeadline: {deadline}\n\n"
        f"Problem statement:\n{statement}\n\n--\n{footer(owner.name)}\n"
    )
    html = _html_page(
        f"<p>Hello,</p><p>{escape(owner_name)} has a task you are set to review.</p>"
        f"<p><strong>Task:</strong> {escape(task.title)}<br>"
        f"<strong>Outcome:</strong> {escape(outcome)}<br>"
        f"<strong>Deadline:</strong> {escape(deadline)}</p>"
        f"<p><strong>Problem statement:</strong><br>{_html_text(statement)}</p>",
        owner.name,
    )
    return subject, text, html


def draft_email(draft: EmailDraft, owner: User) -> tuple[str, str, str]:
    """(subject, text, html) for an approved email task. The body is exactly what the
    person approved, plus the footer."""
    subject = clean_header(draft.subject, 200)
    body = draft.body.strip()
    text = f"{body}\n\n--\n{footer(owner.name)}\n"
    html = _html_page(f"<p>{_html_text(body)}</p>", owner.name)
    return subject, text, html
