"""Scoring checked against a synthetic set whose answers were worked out by hand (lean schema)."""

import pytest

from eval import metrics

TEAMS = ["product", "payments", "platform", "data"]
IST_5PM = "2026-11-02T17:00:00+05:30"
SAME_INSTANT_UTC = "2026-11-02T11:30:00+00:00"  # the same moment as 17:00 IST
TUE_END = "2026-11-03T23:59:00+05:30"


def gold(task_type="normal", dep=None, deadline=None):
    return {"task_type": task_type, "dependency": {"team": dep[0], "direction": dep[1]} if dep else None, "deadline": deadline}


def task(task_type="normal", dep=("none", "none"), deadline=None):
    return {"task_type": task_type, "deadline": deadline, "dependency": {"direction": dep[1], "team": dep[0], "what_is_needed": "x"}, "unknowns": []}


def routing(top1, step_a, model_team, decisive=None, called=False, method="keyword"):
    return {"keyword_decisive": decisive, "keyword_top1": top1, "step_a": step_a,
            "keyword_llm": {"team": model_team, "method": method, "llm_called": called, "latency_ms": 500}}


def pred(tasks, first=True, ok=True, route=None, latency=1000):
    return {"extract": {"tasks": tasks, "first_try_ok": first, "ok": ok, "latency_ms": latency, "tokens_in": 900, "tokens_out": 500, "reasoning_tokens": 200},
            "routing": route}


GOLD = {
    "s1": gold(),
    "s2": gold("email"),
    "s3": gold(dep=("payments", "need_from_them")),
    "s4": gold(dep=("platform", "need_from_them")),
    "s5": gold(),
    "s6": gold(dep=("data", "they_need_from_me")),
    "s7": gold(deadline=IST_5PM),
    "s8": gold(deadline=TUE_END),
    "s9": gold(dep=("platform", "they_need_from_me")),
    "s10": gold(),
    "s11": gold(),
    "s12": gold("email"),
}
PREDS = {
    "s1": pred([task()]),
    "s2": pred([task("normal")], first=False),  # wrong type; JSON needed the retry
    "s3": pred([task(dep=("payments", "need_from_them"))], route=routing("payments", "payments", "payments", decisive="payments")),
    "s4": pred([task()], route=routing("platform", "none", "platform", called=True, method="llm")),  # missed dependency
    "s5": pred([task(dep=("data", "need_from_them"))]),  # false dependency
    "s6": pred([task(dep=("data", "need_from_them"))], route=routing(None, "data", "data", called=True, method="llm")),  # wrong direction
    "s7": pred([task(deadline=SAME_INSTANT_UTC)]),  # same instant as the gold, written differently
    "s8": pred([task()]),  # deadline missing
    "s9": pred([task(dep=("platform", "they_need_from_me"))], route=routing("platform", "platform", "platform", decisive="platform")),
    "s10": pred([task()], first=False, ok=False),  # JSON never valid: fell back
    "s11": pred([task(deadline=TUE_END)]),  # invented a deadline
    "s12": pred([task("email")]),
}


@pytest.fixture(scope="module")
def result():
    return metrics.score(GOLD, PREDS, TEAMS)


def value(rate):
    return (rate["k"], rate["n"])


def test_counts(result):
    assert result["counts"]["items_scored"] == 12 and result["counts"]["items_with_a_gold_dependency"] == 4
    assert result["counts"]["gold_items_without_prediction"] == []


def test_json_validity(result):
    assert value(result["json_valid_first_try"]) == (10, 12)
    assert value(result["json_valid_after_retry"]) == (11, 12)


def test_task_type_accuracy(result):
    assert value(result["task_type_accuracy"]) == (11, 12)


def test_deadline_matching_compares_instants_not_strings(result):
    assert value(result["deadline_exact_match"]) == (10, 12)  # s8 (missing) and s11 (invented) are wrong, s7 is right


def test_dependency_detection(result):
    d = result["dependency"]
    assert (d["f1"]["tp"], d["f1"]["fp"], d["f1"]["fn"]) == (3, 1, 1)
    assert value(d["precision"]) == (3, 4) and value(d["recall"]) == (3, 4) and d["f1"]["value"] == 0.75
    assert value(d["direction_accuracy_on_true_positives"]) == (2, 3)


def test_a_reverse_dependency_found_by_the_model_counts_as_detected(result):
    # s9: another team needs something from me, and the model said so. It must not be mistaken for "none".
    assert result["dependency"]["f1"]["tp"] == 3  # s3, s6 and s9


def test_team_routing_compares_three_methods_on_the_same_notes(result):
    r = result["team_routing"]
    assert r["n_items_with_a_gold_team"] == 4 and r["n_missing_routing_record"] == 0
    assert value(r["keyword_only"]) == (3, 4)  # misses s6, where keywords had no clear winner
    assert value(r["step_a_pick"]) == (3, 4)  # misses s4, where the extract step said "none"
    assert value(r["keyword_plus_model"]) == (4, 4)
    assert value(r["keyword_decisive_rate"]) == (2, 4) and value(r["keyword_accuracy_when_decisive"]) == (2, 2)
    assert value(r["model_called_rate"]) == (2, 4)


def test_latency_and_tokens(result):
    assert result["latency_ms"]["extract_median"]["value"] == 1000
    matcher = result["latency_ms"]["matcher_median_when_model_called"]
    assert matcher["value"] == 500 and matcher["n"] == 2
    assert result["tokens"]["mean_total_per_note"]["value"] == 1400
    assert result["tokens"]["mean_reasoning_per_note"]["value"] == 200


def test_the_dropped_metrics_are_really_gone(result):
    flat = str(result)
    for dropped in ("needs_clarification", "task_count", "multi", "single_exact", "per_predicted_task"):
        assert dropped not in flat


def test_every_rate_carries_a_wilson_interval(result):
    for rate in (result["json_valid_first_try"], result["task_type_accuracy"], result["team_routing"]["keyword_plus_model"]):
        assert rate["ci_method"] == "wilson" and rate["ci_low"] <= rate["value"] <= rate["ci_high"]


# ---- edge cases ---------------------------------------------------------------


def test_a_note_without_a_prediction_is_reported_not_silently_dropped():
    r = metrics.score({"a": gold(), "b": gold()}, {"a": pred([task()])}, TEAMS)
    assert r["counts"]["items_scored"] == 1 and r["counts"]["gold_items_without_prediction"] == ["b"]


def test_a_model_that_returns_no_tasks_scores_as_the_default_empty_task():
    r = metrics.score({"a": gold("email")}, {"a": pred([])}, TEAMS)
    assert value(r["task_type_accuracy"]) == (0, 1)


def test_only_the_first_predicted_task_is_compared_and_extra_tasks_are_not_scored():
    r = metrics.score({"a": gold()}, {"a": pred([task(), task("email", dep=("data", "need_from_them"))])}, TEAMS)
    assert value(r["task_type_accuracy"]) == (1, 1) and r["dependency"]["f1"]["fp"] == 0
    assert metrics.error_items({"a": gold()}, {"a": pred([task(), task("email")])}, {}, TEAMS) == [], "splitting is not evaluated"


def test_missing_routing_records_are_counted_not_assumed_correct():
    g = {"a": gold(dep=("payments", "need_from_them"))}
    r = metrics.score(g, {"a": pred([task(dep=("payments", "need_from_them"))], route=None)}, TEAMS)
    assert r["team_routing"]["n_items_with_a_gold_team"] == 1 and r["team_routing"]["n_missing_routing_record"] == 1
    assert r["team_routing"]["keyword_plus_model"]["n"] == 0


def test_with_no_positive_cases_the_dependency_rates_say_nothing_to_measure():
    r = metrics.score({"a": gold()}, {"a": pred([task()])}, TEAMS)
    assert r["dependency"]["precision"]["value"] is None and r["dependency"]["f1"]["value"] is None


# ---- error analysis -----------------------------------------------------------

NOTES = {i: f"note {i}" for i in GOLD}


def test_error_items_list_exactly_the_wrong_notes_with_factual_categories():
    errors = {e["id"]: e["categories"] for e in metrics.error_items(GOLD, PREDS, NOTES, TEAMS)}
    assert sorted(errors) == sorted(["s2", "s4", "s5", "s6", "s8", "s10", "s11"])
    assert errors["s2"] == ["wrong task type (gold email, predicted normal)"]
    assert errors["s4"] == ["missed dependency"]
    assert errors["s5"] == ["false dependency"]
    assert errors["s6"] == ["wrong dependency direction (gold they_need_from_me, predicted need_from_them)"]
    assert errors["s8"] == [f"deadline: missing, expected {TUE_END}"]
    assert errors["s10"] == ["invalid JSON after retry (fell back to manual review)"]
    assert errors["s11"] == [f"deadline: none expected, got {TUE_END}"]


def test_a_wrong_team_is_reported_with_the_method_that_decided():
    g = {"a": gold(dep=("payments", "need_from_them"))}
    p = {"a": pred([task(dep=("payments", "need_from_them"))], route=routing("data", "payments", "data", called=True, method="llm"))}
    [e] = metrics.error_items(g, p, {"a": "n"}, TEAMS)
    assert e["categories"] == ["wrong team (gold payments, predicted data, decided by llm)"]


def test_a_deadline_that_is_present_but_wrong_is_distinguished():
    g = {"a": gold(deadline=IST_5PM)}
    [e] = metrics.error_items(g, {"a": pred([task(deadline=TUE_END)])}, {}, TEAMS)
    assert e["categories"][0].startswith("deadline: wrong time (expected")


def test_category_counts_group_by_kind():
    counts = metrics.category_counts(metrics.error_items(GOLD, PREDS, NOTES, TEAMS))
    assert counts["deadline"] == 2 and counts["missed dependency"] == 1 and counts["wrong task type"] == 1
    assert sum(counts.values()) == 7 and list(counts.values()) == sorted(counts.values(), reverse=True)


def test_a_perfect_run_has_no_errors():
    g = {"a": gold(), "b": gold("email", dep=("data", "need_from_them"), deadline=IST_5PM)}
    p = {"a": pred([task()]), "b": pred([task("email", dep=("data", "need_from_them"), deadline=IST_5PM)],
                                        route=routing("data", "data", "data", decisive="data"))}
    assert metrics.error_items(g, p, {}, TEAMS) == []
