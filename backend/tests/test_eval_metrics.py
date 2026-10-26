"""Scoring checked against a synthetic set whose answers were worked out by hand."""

import pytest

from eval import metrics

TEAMS = ["product", "payments", "platform", "data"]
IST_5PM = "2026-11-02T17:00:00+05:30"
SAME_INSTANT_UTC = "2026-11-02T11:30:00+00:00"  # the same moment as 17:00 IST
TUE_END = "2026-11-03T23:59:00+05:30"


def gold_single(task_type="normal", dep=None, deadline=None, clar=False):
    direction, team = dep or ("none", "none")
    return {"n_tasks": 1, "task_type": task_type, "has_dependency": dep is not None, "dependency_direction": direction,
            "dependency_team": team, "deadline": deadline, "needs_clarification": clar}


def task(task_type="normal", dep=("none", "none"), deadline=None, unknowns=0):
    return {"task_type": task_type, "deadline": deadline, "dependency": {"direction": dep[0], "team": dep[1], "what_is_needed": "x"},
            "unknowns": [{"field": "priority", "question": "q", "suggestions": []}] * unknowns}


def routing(top1, step_a, model_team, decisive=None, called=False, method="keyword"):
    return {"keyword_decisive": decisive, "keyword_top1": top1, "step_a": step_a,
            "keyword_llm": {"team": model_team, "method": method, "llm_called": called, "latency_ms": 500}}


def pred(tasks, first=True, ok=True, route=None, latency=1000):
    return {"extract": {"tasks": tasks, "first_try_ok": first, "ok": ok, "latency_ms": latency, "tokens_in": 900, "tokens_out": 500, "reasoning_tokens": 200},
            "routing": route}


GOLD = {
    "s1": gold_single(),
    "s2": gold_single("email"),
    "s3": gold_single(dep=("need_from_them", "payments")),
    "s4": gold_single(dep=("need_from_them", "platform")),
    "s5": gold_single(),
    "s6": gold_single(dep=("they_need_from_me", "data")),
    "s7": gold_single(deadline=IST_5PM),
    "s8": gold_single(deadline=TUE_END),
    "s9": gold_single(clar=True),
    "s10": gold_single(),
    "s11": gold_single(dep=("need_from_them", "unknown"), clar=True),
    "s12": gold_single(),
    "m1": {"n_tasks": 3},
    "m2": {"n_tasks": 2},
}
PREDS = {
    "s1": pred([task()]),
    "s2": pred([task("normal")], first=False),  # wrong type; JSON needed the retry
    "s3": pred([task(dep=("need_from_them", "payments"))], route=routing("payments", "payments", "payments", decisive="payments")),
    "s4": pred([task()], route=routing("platform", "none", "platform", called=True, method="llm")),  # missed dependency
    "s5": pred([task(dep=("need_from_them", "data"))]),  # false dependency
    "s6": pred([task(dep=("need_from_them", "data"))], route=routing(None, "data", "data", called=True, method="llm")),  # wrong direction
    "s7": pred([task(deadline=SAME_INSTANT_UTC)]),  # same instant as the gold, written differently
    "s8": pred([task()]),  # deadline missing
    "s9": pred([task(unknowns=2)]),
    "s10": pred([task(unknowns=1)]),  # asked a question it did not need to
    "s11": pred([task(dep=("need_from_them", "unknown"), unknowns=1)], route=routing(None, "unknown", None, called=True, method="llm")),
    "s12": pred([task()], first=False, ok=False),  # JSON never valid: fell back
    "m1": pred([task(), task(), task()]),
    "m2": pred([task(), task(), task()]),  # three tasks for a two-task note
}


@pytest.fixture(scope="module")
def result():
    return metrics.score(GOLD, PREDS, TEAMS)


def value(rate):
    return (rate["k"], rate["n"])


def test_counts(result):
    assert (result["counts"]["items_scored"], result["counts"]["single_task_items"], result["counts"]["multi_task_items"]) == (14, 12, 2)
    assert result["counts"]["gold_items_without_prediction"] == []


def test_json_validity(result):
    assert value(result["json_valid_first_try"]) == (12, 14)
    assert value(result["json_valid_after_retry"]) == (13, 14)


def test_task_type_accuracy_uses_single_task_notes_only(result):
    assert value(result["task_type_accuracy"]) == (11, 12)


def test_dependency_detection(result):
    d = result["dependency"]
    assert (d["f1"]["tp"], d["f1"]["fp"], d["f1"]["fn"]) == (3, 1, 1)
    assert value(d["precision"]) == (3, 4) and value(d["recall"]) == (3, 4)
    assert d["f1"]["value"] == 0.75
    assert value(d["direction_accuracy_on_true_positives"]) == (2, 3)


def test_team_routing_compares_three_methods_on_the_same_notes(result):
    r = result["team_routing"]
    assert r["n_items_with_a_gold_team"] == 3 and r["n_missing_routing_record"] == 0
    assert value(r["keyword_only"]) == (2, 3)  # misses s6, where keywords had no clear winner
    assert value(r["step_a_pick"]) == (2, 3)  # misses s4, where the extract step said "none"
    assert value(r["keyword_plus_model"]) == (3, 3)
    assert value(r["keyword_decisive_rate"]) == (1, 3) and value(r["keyword_accuracy_when_decisive"]) == (1, 1)
    assert value(r["model_called_rate"]) == (2, 3)


def test_a_gold_unknown_team_is_correct_only_when_the_method_names_no_team(result):
    u = result["team_routing"]["gold_unknown_team"]
    assert u["n"] == 1
    for method in ("keyword_only", "step_a_pick", "keyword_plus_model"):
        assert value(u[method]) == (1, 1)


def test_deadline_matching_compares_instants_not_strings(result):
    assert value(result["deadline_exact_match"]) == (11, 12)  # s8 only


def test_needs_clarification(result):
    c = result["needs_clarification"]
    assert value(c["accuracy"]) == (11, 12)
    assert value(c["precision"]) == (2, 3) and value(c["recall"]) == (2, 2)


def test_task_count(result):
    assert value(result["task_count"]["multi_exact_match"]) == (1, 2)
    assert value(result["task_count"]["single_exact_match"]) == (12, 12)


def test_latency_and_tokens(result):
    assert result["latency_ms"]["extract_median"]["value"] == 1000
    assert result["latency_ms"]["matcher_median_when_model_called"]["value"] == 500
    assert result["latency_ms"]["matcher_median_when_model_called"]["n"] == 3
    assert result["tokens"]["mean_total_per_item"]["value"] == 1400
    assert result["tokens"]["mean_reasoning_per_item"]["value"] == 200
    assert result["tokens"]["mean_total_per_predicted_task"]["value"] < 1400, "multi-task notes divide their tokens across tasks"


def test_every_rate_carries_a_wilson_interval(result):
    for rate in (result["json_valid_first_try"], result["task_type_accuracy"], result["team_routing"]["keyword_plus_model"]):
        assert rate["ci_method"] == "wilson" and rate["ci_low"] <= rate["value"] <= rate["ci_high"]


def test_a_reverse_dependency_found_by_the_model_counts_as_a_detected_dependency():
    # "Another team needs something from me" is a dependency too; it must not be mistaken for none.
    gold = {"a": gold_single(dep=("they_need_from_me", "data")), "b": gold_single()}
    preds = {"a": pred([task(dep=("they_need_from_me", "data"))]), "b": pred([task(dep=("they_need_from_me", "payments"))])}
    r = metrics.score(gold, preds, TEAMS)
    assert (r["dependency"]["f1"]["tp"], r["dependency"]["f1"]["fp"], r["dependency"]["f1"]["fn"]) == (1, 1, 0)
    assert value(r["dependency"]["direction_accuracy_on_true_positives"]) == (1, 1)
    assert [e["categories"] for e in metrics.error_items(gold, preds, {}, TEAMS) if e["id"] == "b"] == [["false dependency"]]


# ---- edge cases ---------------------------------------------------------------


def test_a_note_without_a_prediction_is_reported_not_silently_dropped():
    gold = {"a": gold_single(), "b": gold_single()}
    r = metrics.score(gold, {"a": pred([task()])}, TEAMS)
    assert r["counts"]["items_scored"] == 1 and r["counts"]["gold_items_without_prediction"] == ["b"]


def test_a_model_that_returns_no_tasks_scores_as_the_default_empty_task():
    r = metrics.score({"a": gold_single("email")}, {"a": pred([])}, TEAMS)
    assert value(r["task_type_accuracy"]) == (0, 1) and value(r["task_count"]["single_exact_match"]) == (0, 1)


def test_only_the_first_predicted_task_is_compared_for_a_single_task_note():
    r = metrics.score({"a": gold_single()}, {"a": pred([task(), task("email", dep=("need_from_them", "data"))])}, TEAMS)
    assert value(r["task_type_accuracy"]) == (1, 1) and r["dependency"]["f1"]["fp"] == 0
    assert value(r["task_count"]["single_exact_match"]) == (0, 1), "the extra task is counted as a task-count error"


def test_missing_routing_records_are_counted_not_assumed_correct():
    gold = {"a": gold_single(dep=("need_from_them", "payments"))}
    r = metrics.score(gold, {"a": pred([task(dep=("need_from_them", "payments"))], route=None)}, TEAMS)
    assert r["team_routing"]["n_items_with_a_gold_team"] == 1 and r["team_routing"]["n_missing_routing_record"] == 1
    assert r["team_routing"]["keyword_plus_model"]["n"] == 0


def test_with_no_positive_cases_the_dependency_rates_say_nothing_to_measure():
    r = metrics.score({"a": gold_single()}, {"a": pred([task()])}, TEAMS)
    assert r["dependency"]["precision"]["value"] is None and r["dependency"]["f1"]["value"] is None


# ---- error analysis -----------------------------------------------------------

NOTES = {i: f"note {i}" for i in GOLD}


def test_error_items_list_exactly_the_wrong_notes_with_factual_categories():
    errors = {e["id"]: e["categories"] for e in metrics.error_items(GOLD, PREDS, NOTES, TEAMS)}
    assert sorted(errors) == sorted(["m2", "s12", "s2", "s4", "s5", "s6", "s8", "s10"])
    assert errors["s2"] == ["wrong task type (gold email, predicted normal)"]
    assert errors["s4"] == ["missed dependency"]
    assert errors["s5"] == ["false dependency"]
    assert errors["s6"] == ["wrong dependency direction (gold they_need_from_me, predicted need_from_them)"]
    assert errors["s8"] == [f"deadline: missing, expected {TUE_END}"]
    assert errors["s10"] == ["needs-clarification: gold no, model asked 1 question(s)"]
    assert errors["s12"] == ["invalid JSON after retry (fell back to manual review)"]
    assert errors["m2"] == ["wrong task count (gold 2, predicted 3)"]


def test_a_wrong_team_is_reported_with_the_method_that_decided():
    gold = {"a": gold_single(dep=("need_from_them", "payments"))}
    preds = {"a": pred([task(dep=("need_from_them", "payments"))], route=routing("data", "payments", "data", called=True, method="llm"))}
    [e] = metrics.error_items(gold, preds, {"a": "n"}, TEAMS)
    assert e["categories"] == ["wrong team (gold payments, predicted data, decided by llm)"]


def test_a_deadline_that_is_present_but_wrong_is_distinguished():
    gold = {"a": gold_single(deadline=IST_5PM), "b": gold_single()}
    preds = {"a": pred([task(deadline=TUE_END)]), "b": pred([task(deadline=TUE_END)])}
    cats = {e["id"]: e["categories"][0] for e in metrics.error_items(gold, preds, {}, TEAMS)}
    assert cats["a"].startswith("deadline: wrong time (expected") and cats["b"] == f"deadline: none expected, got {TUE_END}"


def test_category_counts_group_by_kind():
    errors = metrics.error_items(GOLD, PREDS, NOTES, TEAMS)
    counts = metrics.category_counts(errors)
    assert counts["wrong task type"] == 1 and counts["missed dependency"] == 1 and counts["deadline"] == 1
    assert sum(counts.values()) == 8
    assert list(counts.values()) == sorted(counts.values(), reverse=True)


def test_a_perfect_run_has_no_errors():
    gold = {"a": gold_single(), "b": {"n_tasks": 2}}
    preds = {"a": pred([task()]), "b": pred([task(), task()])}
    assert metrics.error_items(gold, preds, {}, TEAMS) == []
