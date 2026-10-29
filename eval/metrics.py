"""Scoring: gold labels + saved predictions -> metrics with 95% confidence intervals.

Pure functions. Nothing here calls a model or reads a file, so the maths can be tested on
small synthetic examples whose answers are known by hand.

The lean evaluation scores three things per note, because the notes are all single-task and
a gold label has exactly three fields: task type, dependency (none, or a team plus a
direction) and deadline. NOT evaluated: clarifying questions, and splitting a note into
several tasks. If the model returns more than one task for a note, its FIRST task is the one
compared with the gold label.

Definitions:
  * predicted dependency = the task's dependency.direction is not "none"
  * deadline match       = same instant, or both absent
  * routing is scored on notes whose gold has a dependency, for three methods: keyword-only
    (the highest-scoring team; a tie or no evidence is wrong), the extract step's own pick,
    and keyword evidence plus the model (the matcher the app actually uses).
"""

from collections.abc import Sequence
from datetime import datetime

from eval import stats


def _instant(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def first_task(prediction: dict) -> dict:
    """The task compared with the gold label. If the model returned nothing, an empty default."""
    tasks = prediction["extract"]["tasks"]
    if tasks:
        return tasks[0]
    return {"task_type": "normal", "deadline": None, "dependency": {"direction": "none", "team": "none"}, "unknowns": []}


def predicted_team(prediction: dict, method: str) -> str | None:
    routing = prediction.get("routing")
    if not routing:
        return "<missing>"
    if method == "keyword_plus_model":
        return routing["keyword_llm"]["team"]
    return routing["keyword_top1" if method == "keyword_only" else "step_a"]


def _tokens(prediction: dict) -> int:
    e = prediction["extract"]
    return e["tokens_in"] + e["tokens_out"]


def score(gold: dict[str, dict], predictions: dict[str, dict], team_ids: Sequence[str]) -> dict:
    ids = [i for i in gold if i in predictions]
    result: dict = {
        "counts": {
            "items_scored": len(ids),
            "items_with_a_gold_dependency": sum(gold[i]["dependency"] is not None for i in ids),
            "gold_items_without_prediction": sorted(set(gold) - set(predictions)),
        }
    }

    result["json_valid_first_try"] = stats.rate(sum(predictions[i]["extract"]["first_try_ok"] for i in ids), len(ids))
    result["json_valid_after_retry"] = stats.rate(sum(predictions[i]["extract"]["ok"] for i in ids), len(ids))
    result["task_type_accuracy"] = stats.rate(sum(first_task(predictions[i])["task_type"] == gold[i]["task_type"] for i in ids), len(ids))
    result["deadline_exact_match"] = stats.rate(
        sum(_instant(first_task(predictions[i])["deadline"]) == _instant(gold[i]["deadline"]) for i in ids), len(ids)
    )

    # dependency detection: tp/fp/fn per note, so the F1 interval can resample notes
    per_item, direction_ok, tp = [], 0, 0
    for i in ids:
        predicted_dependency = first_task(predictions[i])["dependency"]
        predicted = predicted_dependency["direction"] != "none"
        actual = gold[i]["dependency"] is not None
        per_item.append((int(predicted and actual), int(predicted and not actual), int(actual and not predicted)))
        if predicted and actual:
            tp += 1
            direction_ok += predicted_dependency["direction"] == gold[i]["dependency"]["direction"]
    tp_all, fp_all, fn_all = (sum(x[k] for x in per_item) for k in range(3))
    result["dependency"] = {
        "precision": stats.rate(tp_all, tp_all + fp_all),
        "recall": stats.rate(tp_all, tp_all + fn_all),
        "f1": stats.f1(per_item),
        "direction_accuracy_on_true_positives": stats.rate(direction_ok, tp),
    }
    result["team_routing"] = _routing(gold, predictions, ids, team_ids)

    latencies = [predictions[i]["extract"]["latency_ms"] for i in ids]
    matcher = [
        predictions[i]["routing"]["keyword_llm"]["latency_ms"]
        for i in ids
        if predictions[i].get("routing") and predictions[i]["routing"]["keyword_llm"]["llm_called"]
    ]
    result["latency_ms"] = {
        "extract_median": stats.summary_stat(latencies, stats.median, "median"),
        "extract_p95": stats.summary_stat(latencies, lambda v: stats.percentile(v, 95), "p95"),
        "matcher_median_when_model_called": stats.summary_stat(matcher, stats.median, "median"),
        "note": "latency is from the original API call; cached reruns reuse the recorded value",
    }
    result["tokens"] = {
        "mean_total_per_note": stats.summary_stat([_tokens(predictions[i]) for i in ids], stats.mean, "mean"),
        "mean_input_per_note": stats.summary_stat([predictions[i]["extract"]["tokens_in"] for i in ids], stats.mean, "mean"),
        "mean_output_per_note": stats.summary_stat([predictions[i]["extract"]["tokens_out"] for i in ids], stats.mean, "mean"),
        "mean_reasoning_per_note": stats.summary_stat([predictions[i]["extract"]["reasoning_tokens"] for i in ids], stats.mean, "mean"),
        "note": "output tokens include reasoning tokens; the extract call only (the matcher is counted separately)",
    }
    return result


def _routing(gold: dict, predictions: dict, ids: list[str], team_ids: Sequence[str]) -> dict:
    methods = ("keyword_only", "step_a_pick", "keyword_plus_model")
    wanted = [i for i in ids if gold[i]["dependency"] is not None and gold[i]["dependency"]["team"] in team_ids]
    with_routing = [i for i in wanted if predictions[i].get("routing")]
    out: dict = {
        "n_items_with_a_gold_team": len(wanted),
        "n_missing_routing_record": len(wanted) - len(with_routing),
    }
    for method in methods:
        out[method] = stats.rate(sum(predicted_team(predictions[i], method) == gold[i]["dependency"]["team"] for i in with_routing), len(with_routing))
    decisive = [i for i in with_routing if predictions[i]["routing"]["keyword_decisive"]]
    out["keyword_decisive_rate"] = stats.rate(len(decisive), len(with_routing))
    out["keyword_accuracy_when_decisive"] = stats.rate(
        sum(predictions[i]["routing"]["keyword_decisive"] == gold[i]["dependency"]["team"] for i in decisive), len(decisive)
    )
    out["model_called_rate"] = stats.rate(sum(predictions[i]["routing"]["keyword_llm"]["llm_called"] for i in with_routing), len(with_routing))
    return out


# ---- error analysis -----------------------------------------------------------


def _deadline_error(expected: str | None, got: str | None) -> str | None:
    if _instant(expected) == _instant(got):
        return None
    if expected is None:
        return f"deadline: none expected, got {got}"
    if got is None:
        return f"deadline: missing, expected {expected}"
    return f"deadline: wrong time (expected {expected}, got {got})"


def error_items(gold: dict[str, dict], predictions: dict[str, dict], notes: dict[str, str], team_ids: Sequence[str]) -> list[dict]:
    """Every note the model got wrong, with a one-line factual category per wrong field."""
    errors = []
    for i in gold:
        if i not in predictions:
            continue
        g, p = gold[i], predictions[i]
        t = first_task(p)
        found: list[str] = []
        if not p["extract"]["ok"]:
            found.append("invalid JSON after retry (fell back to manual review)")
        if t["task_type"] != g["task_type"]:
            found.append(f"wrong task type (gold {g['task_type']}, predicted {t['task_type']})")
        predicted_dependency = t["dependency"]["direction"] != "none"
        if g["dependency"] and not predicted_dependency:
            found.append("missed dependency")
        elif predicted_dependency and not g["dependency"]:
            found.append("false dependency")
        elif g["dependency"] and t["dependency"]["direction"] != g["dependency"]["direction"]:
            found.append(f"wrong dependency direction (gold {g['dependency']['direction']}, predicted {t['dependency']['direction']})")
        if g["dependency"] and g["dependency"]["team"] in team_ids and p.get("routing"):
            got = p["routing"]["keyword_llm"]
            if got["team"] != g["dependency"]["team"]:
                found.append(f"wrong team (gold {g['dependency']['team']}, predicted {got['team']}, decided by {got['method']})")
        deadline = _deadline_error(g["deadline"], t["deadline"])
        if deadline:
            found.append(deadline)
        if found:
            errors.append({"id": i, "note": notes.get(i, ""), "categories": found})
    return errors


def category_counts(errors: list[dict]) -> dict[str, int]:
    """Counts by the category's first words (the part before any parenthesis or colon detail)."""
    counts: dict[str, int] = {}
    for e in errors:
        for category in e["categories"]:
            key = category.split(" (")[0].split(":")[0]
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
