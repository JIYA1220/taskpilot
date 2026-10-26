"""The three organize steps. Step A (extract) and Step C (rewrite) call the LLM;
Step B (choosing which questions to ask) is plain code, so it is deterministic and
safe to re-run when the graph resumes from its interrupt."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import EmailStr, TypeAdapter, ValidationError

from backend.config import Settings
from backend.directory import Directory
from backend.llm import GroqLLM, load_prompt
from backend.organize.schemas import (
    Clarification,
    ExtractedTask,
    ExtractOutput,
    ProposedTask,
    Question,
    RewriteOutput,
)

MAX_QUESTIONS = 3
# Which unknowns are worth a question first. A blocked dependency or a missing
# deadline matters more than a nice-to-know reviewer.
FIELD_ORDER = {"dependency": 0, "deadline": 1, "priority": 2, "reviewer": 3, "scope": 4}

_email = TypeAdapter(EmailStr)


def _offset(dt: datetime) -> str:
    raw = dt.strftime("%z")
    return f"{raw[:3]}:{raw[3:]}"


def describe_now(now: datetime, tz_name: str) -> str:
    local = now.astimezone(ZoneInfo(tz_name))
    return f"{local:%A %d %B %Y, %H:%M} (timezone {tz_name}, UTC{_offset(local)})"


def describe_directory(directory: Directory) -> str:
    lines = []
    for team in directory.teams:
        members = ", ".join(u.name for u in directory.users if u.team_id == team.id)
        lines.append(f"- {team.id} ({team.name}): owns {', '.join(team.owns)}; members: {members}")
    return "\n".join(lines)


# ---- Step A -----------------------------------------------------------------


def build_extract_message(settings: Settings, directory: Directory, user_team_id: str, raw_text: str, now: datetime) -> str:
    """The user message for Step A. Shared with the evaluation so its token estimates use the real prompt."""
    return (
        f"Current date and time: {describe_now(now, settings.timezone)}\n"
        f"The person's own team: {user_team_id}\n"
        f"Teams (use these ids for dependency.team):\n{describe_directory(directory)}\n\n"
        f"<notes>\n{raw_text}\n</notes>"
    )


def extract_tasks(
    llm: GroqLLM,
    settings: Settings,
    directory: Directory,
    user_team_id: str,
    raw_text: str,
    now: datetime,
) -> tuple[list[ExtractedTask], list[str]]:
    user = build_extract_message(settings, directory, user_team_id, raw_text, now)
    result = llm.generate(
        model=settings.groq_main_model,
        prompt=load_prompt("extract"),
        user=user,
        output=ExtractOutput,
        context={"team_ids": directory.team_ids()},
    )
    if result.ok and result.parsed:
        return result.parsed.tasks, []
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()][:20] or [raw_text.strip()]
    fallback = [
        ExtractedTask(title=line[:160], source_text=line, needs_manual_review=True) for line in lines
    ]
    return fallback, ["The AI response could not be used, so each line became a task for manual review."]


# ---- Step B -----------------------------------------------------------------


def select_questions(tasks: list[ExtractedTask], limit: int = MAX_QUESTIONS) -> list[Question]:
    candidates = []
    seen: set[tuple[str, str]] = set()
    for index, task in enumerate(tasks):
        if task.needs_manual_review:
            continue
        for unknown in task.unknowns:
            key = (unknown.field, unknown.question.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            candidates.append((FIELD_ORDER[unknown.field], index, unknown, task))
    candidates.sort(key=lambda c: (c[0], c[1]))  # stable: ties keep the order written
    return [
        Question(
            id=f"q{n}",
            task_index=index,
            task_title=task.title,
            field=unknown.field,
            question=unknown.question,
            suggestions=unknown.suggestions,
        )
        for n, (_, index, unknown, task) in enumerate(candidates[:limit])
    ]


def clean_answers(questions: list[Question], answers: dict | None) -> dict[str, str | None]:
    """Keep only answers to questions we asked; blank means skipped."""
    cleaned: dict[str, str | None] = {}
    for q in questions:
        value = (answers or {}).get(q.id)
        value = value.strip()[:300] if isinstance(value, str) else None
        cleaned[q.id] = value or None
    return cleaned


# ---- Step C -----------------------------------------------------------------


def resolve_reviewer(answer: str | None, directory: Directory) -> str | None:
    """A reviewer address comes only from the person's own answer: a typed email or a
    directory name. It is never taken from model output."""
    if not answer:
        return None
    text = answer.strip()
    try:
        return str(_email.validate_python(text)).lower()
    except ValidationError:
        pass
    wanted = text.lower()
    exact = [u for u in directory.users if u.name.lower() == wanted]
    first = [u for u in directory.users if u.name.lower().split()[0] == wanted]
    matches = exact or (first if len(first) == 1 else [])
    return str(matches[0].email).lower() if matches else None


def format_problem_statement(goal: str, done: str, inputs: list[str], deadline: datetime | None, tz_name: str) -> str:
    lines = [f"Goal: {goal.strip()}", f"Done when: {done.strip()}"]
    if inputs:
        lines.append("Needs: " + "; ".join(i.strip() for i in inputs))
    if deadline:
        lines.append(f"Deadline: {deadline.astimezone(ZoneInfo(tz_name)):%a %d %b %H:%M %Z}")
    else:
        lines.append("Deadline: none stated")
    return "\n".join(lines)


def rewrite_tasks(
    llm: GroqLLM,
    settings: Settings,
    directory: Directory,
    tasks: list[ExtractedTask],
    answered: dict[int, list[dict]],
    now: datetime,
) -> list[ProposedTask]:
    """`answered[task_index]` is a list of {field, question, answer} (answer may be None)."""
    tz = settings.timezone
    usable = [(i, t) for i, t in enumerate(tasks) if not t.needs_manual_review]
    rewritten = {}
    if usable:
        payload = [
            {
                "index": position,
                "title": task.title,
                "task_type": task.task_type,
                "deadline": task.deadline.isoformat() if task.deadline else None,
                "estimated_minutes": task.estimated_minutes,
                "dependency": task.dependency.model_dump(),
                "answers": [
                    {"question": a["question"], "answer": a["answer"]} for a in answered.get(i, [])
                ],
            }
            for position, (i, task) in enumerate(usable)
        ]
        user = (
            f"Current date and time: {describe_now(now, tz)}\n"
            f"Tasks:\n{json.dumps(payload, indent=1)}"
        )
        result = llm.generate(
            model=settings.groq_main_model,
            prompt=load_prompt("rewrite"),
            user=user,
            output=RewriteOutput,
            context={"n_tasks": len(usable)},
        )
        if result.ok and result.parsed:
            rewritten = {usable[r.index][0]: r for r in result.parsed.tasks}

    proposed = []
    for i, task in enumerate(tasks):
        qa = answered.get(i, [])
        reviewer = next(
            (resolve_reviewer(a["answer"], directory) for a in qa if a["field"] == "reviewer" and a["answer"]),
            None,
        )
        clarifications = [Clarification(question=a["question"], answer=a["answer"]) for a in qa]
        r = rewritten.get(i)
        deadline = task.deadline
        if r and r.deadline and any(a["field"] == "deadline" and a["answer"] for a in qa):
            deadline = r.deadline
        proposed.append(
            ProposedTask(
                source_text=task.source_text,
                title=task.title,
                task_type=task.task_type,
                deadline=deadline,
                estimated_minutes=task.estimated_minutes,
                priority=r.priority if r else "P2",
                priority_reason=r.priority_reason if r else "Needs manual review: the AI response could not be used.",
                problem_statement=format_problem_statement(r.goal, r.definition_of_done, r.inputs_needed, deadline, tz)
                if r
                else "",
                reviewer_email=reviewer,
                dependency_hint=task.dependency,
                needs_manual_review=r is None,
                clarifications=clarifications,
            )
        )
    return proposed
