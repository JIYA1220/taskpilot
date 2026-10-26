"""Drafting email tasks. The model writes the words; code decides everything else, above all
who the email goes to."""

import re

from pydantic import BaseModel, Field, field_validator

from backend.config import Settings
from backend.directory import Directory, UserEntry
from backend.llm import GroqLLM, load_prompt
from backend.models import EmailDraft, Task, User

# Links and addresses are never allowed in a model-written draft. Matching is deliberately
# broad: a false alarm just triggers the retry, which asks the model to rewrite without them.
_LINK = re.compile(r"https?://|ftp://|www\.|\b[\w-]+\.(?:com|org|net|io|in|co|dev|app|ai)\b", re.IGNORECASE)
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
ATTACH_PLACEHOLDER = "[attach"


class DraftOutput(BaseModel):
    subject: str = Field(min_length=1, max_length=150)
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("subject", "body")
    @classmethod
    def _no_links_or_addresses(cls, value: str) -> str:
        if _LINK.search(value) or _ADDRESS.search(value):
            raise ValueError("must not contain links, URLs or email addresses")
        return value.strip()

    @field_validator("subject")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("subject must be a single line")
        return value


def _named_in(text: str, candidates: list[UserEntry]) -> list[UserEntry]:
    """People from `candidates` that `text` names: a full name, or a first name that
    identifies exactly one candidate."""
    text = text.lower()
    words = set(re.findall(r"[a-z]+", text))  # letters only, so "ravi's" still yields "ravi"
    found = []
    for user in candidates:
        full = user.name.lower()
        first = full.split()[0]
        first_unique = sum(1 for c in candidates if c.name.lower().split()[0] == first) == 1
        if full in text or (first in words and first_unique):
            found.append(user)
    return found


def find_recipient(task: Task, directory: Directory, owner: User) -> UserEntry | None:
    """Match a person named in the task to the directory. Returns None when nobody (or
    several people) match, in which case the sender chooses. The model's opinion is never
    used for this.

    The person's own words (title and original note) decide first. Only if they name nobody
    do we look at the AI-written statement, and there the reviewer is ignored: a reviewer
    checks the task, they are not who the email goes to."""
    candidates = [u for u in directory.users if u.email.lower() != owner.email.lower()]
    named = _named_in(" ".join(filter(None, [task.title, task.raw_text])), candidates)
    if named:
        return named[0] if len(named) == 1 else None
    reviewer = (task.reviewer_email or "").lower()
    fallback = _named_in(task.problem_statement or "", [u for u in candidates if u.email.lower() != reviewer])
    return fallback[0] if len(fallback) == 1 else None


def _goal_line(problem_statement: str) -> str:
    for line in (problem_statement or "").splitlines():
        if line.lower().startswith("goal:"):
            return line[5:].strip()
    return ""


def is_org_address(address: str, org_domain: str) -> bool:
    """Exact domain match. 'evilacme.example.com' and 'acme.example.com.evil.io' do not count."""
    return address.rsplit("@", 1)[-1].strip().lower() == org_domain.strip().lower() if "@" in address else False


def generate_draft(
    llm: GroqLLM, settings: Settings, directory: Directory, task: Task, owner: User
) -> EmailDraft:
    recipient = find_recipient(task, directory, owner)
    # Only the title, the person's original words and the goal. The rest of the AI-written
    # statement (review steps, inputs) describes other work and once leaked into a draft as
    # an invented fact ("Meera has reviewed the report").
    details = [f"Title: {task.title}"]
    if task.raw_text:
        details.append(f"Original note: {task.raw_text}")
    if _goal_line(task.problem_statement):
        details.append(f"Goal: {_goal_line(task.problem_statement)}")
    user_message = (
        f"Sender: {owner.name}\n"
        f"Recipient: {recipient.name if recipient else 'unknown'}\n"
        f"Organization: {directory.org_name}\n\n"
        "<task>\n" + "\n".join(details) + "\n</task>"
    )
    result = llm.generate(
        model=settings.groq_main_model,
        prompt=load_prompt("draft_email"),
        user=user_message,
        output=DraftOutput,
        max_tokens=1500,
    )
    draft = EmailDraft(
        task_id=task.id,
        to_email=str(recipient.email).lower() if recipient else None,
        to_source="directory" if recipient else "",
    )
    if result.ok and result.parsed:
        draft.subject, draft.body = result.parsed.subject, result.parsed.body
    else:  # the AI draft was unusable twice: hand over an empty draft to write by hand
        draft.subject, draft.body, draft.needs_manual_review = task.title[:150], "", True
    return draft
