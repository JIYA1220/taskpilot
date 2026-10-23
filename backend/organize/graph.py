"""extract -> clarify (pauses for the person's answers) -> rewrite.

The graph is checkpointed in SQLite, so a paused organize run survives a server
restart. The clarify node runs again from its top on resume, which is why Step B
is deterministic code and not an LLM call."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from backend.clock import Clock
from backend.config import Settings
from backend.directory import Directory
from backend.llm import GroqLLM
from backend.organize.schemas import ExtractedTask, ProposedTask, Question
from backend.organize.steps import (
    clean_answers,
    extract_tasks,
    rewrite_tasks,
    select_questions,
)


class OrganizeState(TypedDict, total=False):
    raw_text: str
    user_team_id: str
    now: str
    tasks: list[dict]
    warnings: list[str]
    questions: list[dict]
    answers: dict
    proposed: list[dict]


@dataclass
class GraphDeps:
    llm: GroqLLM
    settings: Settings
    directory: Directory
    clock: Clock


class Outcome(BaseModel):
    thread_id: str
    status: str  # "needs_answers" or "ready"
    extracted_titles: list[str] = []
    questions: list[Question] = []
    tasks: list[ProposedTask] = []
    warnings: list[str] = []


def build_graph(deps: GraphDeps, checkpointer: BaseCheckpointSaver):
    def extract_node(state: OrganizeState) -> dict:
        now = deps.clock()
        tasks, warnings = extract_tasks(
            deps.llm, deps.settings, deps.directory, state["user_team_id"], state["raw_text"], now
        )
        return {
            "now": now.isoformat(),
            "tasks": [t.model_dump(mode="json") for t in tasks],
            "warnings": warnings,
        }

    def clarify_node(state: OrganizeState) -> dict:
        tasks = [ExtractedTask.model_validate(t) for t in state["tasks"]]
        questions = select_questions(tasks)
        if not questions:
            return {"questions": [], "answers": {}}
        reply = interrupt({"questions": [q.model_dump() for q in questions]})
        return {
            "questions": [q.model_dump() for q in questions],
            "answers": clean_answers(questions, (reply or {}).get("answers")),
        }

    def rewrite_node(state: OrganizeState) -> dict:
        tasks = [ExtractedTask.model_validate(t) for t in state["tasks"]]
        questions = [Question.model_validate(q) for q in state.get("questions", [])]
        answers = state.get("answers", {})
        answered: dict[int, list[dict]] = {}
        for q in questions:
            answered.setdefault(q.task_index, []).append(
                {"field": q.field, "question": q.question, "answer": answers.get(q.id)}
            )
        proposed = rewrite_tasks(
            deps.llm,
            deps.settings,
            deps.directory,
            tasks,
            answered,
            datetime.fromisoformat(state["now"]),
        )
        return {"proposed": [p.model_dump(mode="json") for p in proposed]}

    graph = StateGraph(OrganizeState)
    graph.add_node("extract", extract_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", "clarify")
    graph.add_edge("clarify", "rewrite")
    graph.add_edge("rewrite", END)
    return graph.compile(checkpointer=checkpointer)


def _outcome(thread_id: str, result: dict[str, Any]) -> Outcome:
    titles = [t["title"] for t in result.get("tasks", [])]
    warnings = result.get("warnings", [])
    paused = result.get("__interrupt__")
    if paused:
        questions = [Question.model_validate(q) for q in paused[0].value["questions"]]
        return Outcome(
            thread_id=thread_id,
            status="needs_answers",
            extracted_titles=titles,
            questions=questions,
            warnings=warnings,
        )
    return Outcome(
        thread_id=thread_id,
        status="ready",
        extracted_titles=titles,
        tasks=[ProposedTask.model_validate(p) for p in result.get("proposed", [])],
        warnings=warnings,
    )


def start_organize(graph, thread_id: str, raw_text: str, user_team_id: str) -> Outcome:
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke({"raw_text": raw_text, "user_team_id": user_team_id}, config)
    return _outcome(thread_id, result)


def is_paused(graph, thread_id: str) -> bool:
    return bool(graph.get_state({"configurable": {"thread_id": thread_id}}).next)


def resume_organize(graph, thread_id: str, answers: dict) -> Outcome:
    config = {"configurable": {"thread_id": thread_id}}
    # Wrapped: an empty dict (every question skipped) is not accepted as a resume value.
    result = graph.invoke(Command(resume={"answers": answers}), config)
    return _outcome(thread_id, result)
