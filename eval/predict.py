"""Run the app's real code on one note and record what it did, as plain data.

The same functions the app uses (Step A extraction and the team matcher) run here, so the
evaluation measures the product and not a lookalike. The model sees only the note. Gold labels
are used for one thing only: choosing which notes also get the routing comparison."""

from backend.config import Settings
from backend.conflicts.matching import keyword_pick, keyword_scores, match_team
from backend.directory import Directory
from backend.llm import AuditEntry, GroqLLM, Transport
from backend.organize.steps import extract_tasks
from eval.labels import FIXED_NOW

OWN_TEAM = "product"  # the person labeling and "using" the app is on the product team


class Recorder:
    """Collects the audit entry of every LLM attempt so latency and tokens can be attributed to a step."""

    def __init__(self):
        self.entries: list[AuditEntry] = []

    def sink(self, entry: AuditEntry) -> None:
        self.entries.append(entry)


def settings_for(base: Settings, model: str) -> Settings:
    """The same settings with `model` doing every job, so each model is measured end to end."""
    return base.model_copy(update={"groq_main_model": model, "groq_fast_model": model})


def keyword_top1(scores: dict[str, float]) -> str | None:
    """Keyword-only routing: the highest-scoring team. No evidence or a tie for first means no answer."""
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if not ranked or ranked[0][1] <= 0:
        return None
    if len(ranked) > 1 and ranked[1][1] == ranked[0][1]:
        return None
    return ranked[0][0]


def _usage(entries: list[AuditEntry]) -> dict:
    return {
        "attempts": len(entries),
        "first_try_ok": bool(entries and entries[0].parsed_ok),
        "ok": any(e.parsed_ok for e in entries),
        "latency_ms": sum(e.latency_ms or 0 for e in entries),
        "tokens_in": sum(e.tokens_in or 0 for e in entries),
        "tokens_out": sum(e.tokens_out or 0 for e in entries),
        "reasoning_tokens": sum(e.reasoning_tokens or 0 for e in entries),
    }


def wants_routing(first_task: dict | None, gold: dict | None, team_ids: list[str]) -> bool:
    """Which notes get the routing comparison: single-task notes whose gold names a directory team
    (so every method is scored on the same items), plus any note where the model itself found a dependency."""
    if gold is not None and gold["n_tasks"] > 1:
        return False
    if gold is not None and gold["has_dependency"] and gold["dependency_team"] in team_ids:
        return True
    return bool(first_task and first_task["dependency"]["direction"] != "none")


def predict_item(
    transport: Transport,
    settings: Settings,
    directory: Directory,
    item_id: str,
    note: str,
    gold: dict | None = None,
) -> dict:
    recorder = Recorder()
    llm = GroqLLM(transport, recorder.sink)
    tasks, warnings = extract_tasks(llm, settings, directory, OWN_TEAM, note, FIXED_NOW)
    extract = {**_usage(recorder.entries), "tasks": [t.model_dump(mode="json") for t in tasks], "warnings": warnings}

    first = extract["tasks"][0] if extract["tasks"] else None
    routing = None
    if wants_routing(first, gold, directory.team_ids()):
        dependency = first["dependency"] if first else {"what_is_needed": "", "team": "none"}
        text = f"{note} {dependency['what_is_needed']}".strip()
        scores = keyword_scores(text, directory, OWN_TEAM)
        before = len(recorder.entries)
        hint = dependency["team"] if dependency["team"] in directory.team_ids() else None
        match = match_team(llm, settings, directory, OWN_TEAM, text, hint)
        called = recorder.entries[before:]
        routing = {
            "text": text,
            "keyword_scores": scores,
            "keyword_decisive": keyword_pick(scores),
            "keyword_top1": keyword_top1(scores),
            "step_a": dependency["team"],
            "keyword_llm": {"team": match.team_id, "method": match.method, "llm_called": bool(called), **_usage(called)},
        }
    return {"id": item_id, "model": settings.groq_main_model, "extract": extract, "routing": routing}
