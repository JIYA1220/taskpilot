"""Scripted stand-in for the Groq network call, plus canned model replies."""

import json
from datetime import UTC, datetime

from backend.llm import RawCompletion

# 2026-11-02 09:00 in Asia/Kolkata, the fixed "now" used across tests and the evaluation.
FIXED_NOW = datetime(2026, 11, 2, 3, 30, tzinfo=UTC)


class FakeTransport:
    """Replies are queued per prompt kind ("extract", "rewrite" or "draft"); the last one repeats."""

    def __init__(self, extract=(), rewrite=(), draft=()):
        self.queues = {"extract": list(extract), "rewrite": list(rewrite), "draft": list(draft)}
        self.calls: list[dict] = []

    def __call__(self, model, messages, max_tokens) -> RawCompletion:
        system = messages[0]["content"]
        kind = "extract" if system.startswith("You turn") else "draft" if system.startswith("You draft") else "rewrite"
        self.calls.append({"kind": kind, "model": model, "messages": [dict(m) for m in messages]})
        queue = self.queues[kind]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return RawCompletion(content=content, tokens_in=100, tokens_out=50, reasoning_tokens=10, latency_ms=5)

    def count(self, kind: str) -> int:
        return sum(1 for c in self.calls if c["kind"] == kind)


class FakeSender:
    """Collects emails instead of sending them. `fail_next` makes the next sends raise."""

    def __init__(self):
        self.sent: list = []
        self._failures: list = []

    def fail_next(self, count: int = 1, retryable: bool = True, message: str = "connection refused"):
        from backend.mail.sender import EmailSendError

        self._failures += [EmailSendError(message, retryable=retryable)] * count

    def raise_next(self, exc: Exception):
        self._failures.append(exc)

    def send(self, message) -> None:
        if self._failures:
            raise self._failures.pop(0)
        self.sent.append(message)


def extract_reply(*tasks: dict) -> dict:
    return {"tasks": list(tasks)}


REFUND = {
    "title": "Ask payments team for the refund API",
    "source_text": "ask payments team for the refund API",
    "task_type": "normal",
    "deadline": None,
    "estimated_minutes": 15,
    "dependency": {"direction": "need_from_them", "team": "payments", "what_is_needed": "refund API access"},
    "unknowns": [
        {"field": "deadline", "question": "When do you need the refund API?", "suggestions": ["Today 17:00", "Tomorrow morning"]},
        {"field": "priority", "question": "How urgent is the refund API request?", "suggestions": ["P1", "P2", "P3"]},
    ],
}
REPORT = {
    "title": "Send weekly report to Ravi",
    "source_text": "send weekly report to Ravi by 5",
    "task_type": "email",
    "deadline": "2026-11-02T17:00:00+05:30",
    "estimated_minutes": 20,
    "dependency": {"direction": "none", "team": "none", "what_is_needed": ""},
    "unknowns": [
        {"field": "reviewer", "question": "Who should review this?", "suggestions": ["Aarav Singh", "Ravi Kumar"]},
    ],
}
NO_QUESTIONS = {**REPORT, "unknowns": []}


def rewrite_reply(*entries: dict) -> dict:
    defaults = {
        "goal": "Get the outcome done",
        "definition_of_done": "It is finished and confirmed",
        "inputs_needed": [],
        "priority": "P2",
        "priority_reason": "Normal priority.",
        "deadline": None,
    }
    return {"tasks": [{**defaults, "index": i, **e} for i, e in enumerate(entries)]}
