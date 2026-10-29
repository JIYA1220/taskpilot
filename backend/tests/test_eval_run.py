"""The evaluation runner with a scripted fake model: no network, no real labels, no test-set notes."""

import json
import math
from argparse import Namespace
from datetime import UTC, datetime

import pytest

from backend.config import Settings
from backend.directory import load_directory
from backend.llm import RawCompletion
from eval import cache, run_eval
from eval import labels as store
from eval.cache import CachingTransport, EvalInfraError, Ledger
from eval.run_eval import Paths

SETTINGS = Settings(_env_file=None, app_env="test", groq_api_key="not-a-real-key")
DIRECTORY = load_directory(SETTINGS.directory_path)
MODELS = ["model-a", "model-b"]
FIVE_PM = "2026-11-02T17:00:00+05:30"
TUE_END = "2026-11-03T23:59:00+05:30"

NOTES = {
    "n1": "ask the payments team for the refund api documentation",
    "n2": "send the weekly report to ravi by 5",
    "n3": "update the readme",
    "n4": "fix the typo tomorrow",
}
ITEMS = [{"id": i, "input": text} for i, text in NOTES.items()]


def one(title, task_type="normal", dep=("none", "none", ""), deadline=None):
    return {
        "title": title, "source_text": title, "task_type": task_type, "deadline": deadline, "estimated_minutes": None,
        "dependency": {"direction": dep[0], "team": dep[1], "what_is_needed": dep[2]},
        "unknowns": [],
    }


REPLIES = {
    NOTES["n1"]: {"tasks": [one("Ask payments for the refund API", dep=("need_from_them", "payments", "refund API documentation"))]},
    NOTES["n2"]: {"tasks": [one("Send the weekly report", "email", deadline=FIVE_PM)]},
    NOTES["n3"]: {"tasks": [one("Update the readme")]},
    NOTES["n4"]: {"tasks": [one("Fix the typo", deadline=TUE_END)]},
}


def label(task_type="normal", dep=None, deadline=None):
    return {"task_type": task_type, "dependency": {"team": dep[0], "direction": dep[1]} if dep else None, "deadline": deadline}


GOLD = {
    "n1": label(dep=("payments", "need_from_them")),
    "n2": label("email", deadline=FIVE_PM),
    "n3": label(),
    "n4": label(deadline=TUE_END),
}


class FakeModel:
    """Answers Step A from a table and the matcher with a fixed team. Counts every call it gets."""

    def __init__(self, replies=REPLIES, fail_after: int | None = None):
        self.replies, self.calls, self.fail_after = replies, [], fail_after

    def __call__(self, model, messages, max_tokens):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise EvalInfraError("quota exhausted")
        system, user = messages[0]["content"], messages[1]["content"]
        self.calls.append((model, system[:10]))
        if system.startswith("You turn"):
            note = user.split("<notes>\n")[1].split("\n</notes>")[0]
            body = self.replies.get(note, {"tasks": [one("Something")]})
        else:
            body = {"team": "data", "reason": "guess"}
        return RawCompletion(json.dumps(body), 900, 400, 100, 1200)


@pytest.fixture
def paths(tmp_path):
    return Paths(tmp_path / "results", tmp_path / "cache")


def transport(paths, model=None, **kw):
    ledger = Ledger()
    model = model or FakeModel()
    return CachingTransport(model, paths.cache, SETTINGS.groq_reasoning_effort, ledger, **kw), ledger, model


def run(paths, gold=GOLD, models=MODELS, items=ITEMS, **kw):
    t, ledger, fake = transport(paths, **kw)
    stopped = run_eval.run_models(models, items, gold, DIRECTORY, SETTINGS, t, paths, announce=lambda *_: None)
    return stopped, ledger, fake


# ---- small rules that decide what gets scored ----------------------------------


def test_keyword_only_routing_answers_only_when_there_is_a_single_best_team():
    from eval.predict import keyword_top1

    assert keyword_top1({"a": 2.0, "b": 1.0}) == "a" and keyword_top1({"a": 0.5}) == "a"
    assert keyword_top1({"a": 1.0, "b": 1.0}) is None, "a tie is not an answer"
    assert keyword_top1({"a": 0, "b": 0}) is None and keyword_top1({}) is None


@pytest.mark.parametrize(
    "predicted_dep,gold,expected",
    [
        (False, {"dependency": {"team": "payments", "direction": "need_from_them"}}, True),  # even if the model found nothing
        (False, {"dependency": None}, False),
        (True, {"dependency": None}, True),  # a false alarm is still routed
        (True, None, True),  # no labels: follow the model
        (False, None, False),
    ],
)
def test_which_notes_get_the_routing_comparison(predicted_dep, gold, expected):
    from eval.predict import wants_routing

    task = {"dependency": {"direction": "need_from_them" if predicted_dep else "none"}}
    assert wants_routing(task, gold, DIRECTORY.team_ids()) is expected


def test_a_model_that_returned_no_task_still_gets_routed_when_the_gold_has_a_team():
    from eval.predict import wants_routing

    gold = {"dependency": {"team": "data", "direction": "they_need_from_me"}}
    assert wants_routing(None, gold, DIRECTORY.team_ids()) is True
    assert wants_routing(None, None, DIRECTORY.team_ids()) is False


# ---- running ------------------------------------------------------------------


def test_every_note_is_predicted_by_every_model_and_saved(paths):
    stopped, ledger, fake = run(paths)
    assert stopped is None
    for model in MODELS:
        records = run_eval.read_predictions(paths, model)
        assert list(records) == ["n1", "n2", "n3", "n4"] and all(r["model"] == model for r in records.values())
        n2 = records["n2"]["extract"]
        assert n2["first_try_ok"] and n2["ok"] and n2["attempts"] == 1 and (n2["tokens_in"], n2["tokens_out"], n2["reasoning_tokens"], n2["latency_ms"]) == (900, 400, 100, 1200)
        assert [t["task_type"] for t in n2["tasks"]] == ["email"]
        assert datetime.fromisoformat(n2["tasks"][0]["deadline"]) == datetime(2026, 11, 2, 11, 30, tzinfo=UTC)  # 17:00 IST
    assert {m for m, _ in fake.calls} == set(MODELS), "each model really did the work, end to end"
    assert ledger.api_calls == 8 and ledger.cache_hits == 0


def test_routing_is_recorded_for_the_notes_with_a_gold_dependency_only(paths):
    run(paths)
    records = run_eval.read_predictions(paths, "model-a")
    routing = records["n1"]["routing"]
    assert routing["keyword_decisive"] == "payments" and routing["keyword_top1"] == "payments" and routing["step_a"] == "payments"
    assert routing["keyword_llm"] == {**routing["keyword_llm"], "team": "payments", "method": "keyword", "llm_called": False, "attempts": 0}
    assert records["n2"]["routing"] is None and records["n3"]["routing"] is None and records["n4"]["routing"] is None


def test_without_gold_labels_routing_follows_what_the_model_found(paths):
    run(paths, gold={})
    records = run_eval.read_predictions(paths, "model-a")
    assert records["n1"]["routing"] is not None and records["n2"]["routing"] is None


def test_the_matcher_asks_the_model_when_keywords_are_not_decisive(paths):
    replies = {**REPLIES, NOTES["n3"]: {"tasks": [one("Fix the dashboard", dep=("need_from_them", "unknown", "dashboard fix"))]}}
    gold = {**GOLD, "n3": label(dep=("data", "need_from_them"))}
    t, ledger, fake = transport(paths, FakeModel(replies))
    run_eval.run_models(["model-a"], ITEMS, gold, DIRECTORY, SETTINGS, t, paths, announce=lambda *_: None)
    route = run_eval.read_predictions(paths, "model-a")["n3"]["routing"]
    assert route["keyword_decisive"] is None and route["keyword_llm"]["llm_called"] is True and route["keyword_llm"]["method"] == "llm"
    assert route["keyword_llm"]["team"] == "data" and route["keyword_llm"]["tokens_in"] == 900


def test_a_rerun_costs_nothing(paths):
    run(paths)
    stopped, ledger, fake = run(paths)
    assert stopped is None and fake.calls == [] and ledger.api_calls == 0 and ledger.cache_hits == 8
    assert ledger.hit_rate == 1.0


# ---- stopping and resuming ----------------------------------------------------


def test_a_quota_error_stops_cleanly_keeps_progress_and_resumes_without_repeating_work(paths):
    stopped, first_ledger, _ = run(paths, model=FakeModel(fail_after=2))
    assert "stopped on model-a after 2 of 4 notes" in stopped and "quota exhausted" in stopped
    assert list(run_eval.read_predictions(paths, "model-a")) == ["n1", "n2"]
    assert run_eval.read_predictions(paths, "model-b") == {}

    stopped, second_ledger, _ = run(paths)  # the next day, quota is back
    assert stopped is None
    assert first_ledger.api_calls == 2 and second_ledger.cache_hits == 2 and second_ledger.api_calls == 6, "the first two answers were not paid for twice"
    assert len(run_eval.read_predictions(paths, "model-b")) == 4


def test_a_quota_error_is_not_scored_as_a_wrong_answer(paths):
    stopped, _, _ = run(paths, model=FakeModel(fail_after=0))
    assert stopped and run_eval.read_predictions(paths, "model-a") == {}


def test_invalid_json_is_scored_as_the_models_failure_not_an_infrastructure_problem(paths):
    class BadJson(FakeModel):
        def __call__(self, model, messages, max_tokens):
            super().__call__(model, messages, max_tokens)
            return RawCompletion("not json at all", 900, 400, 0, 800)

    stopped, _, _ = run(paths, model=BadJson(), items=ITEMS[:1], gold=GOLD, models=["model-a"])
    assert stopped is None
    extract = run_eval.read_predictions(paths, "model-a")["n1"]["extract"]
    assert (extract["first_try_ok"], extract["ok"], extract["attempts"]) == (False, False, 2)
    assert extract["warnings"] and extract["tasks"][0]["needs_manual_review"] is True
    assert extract["tokens_in"] == 1800 and extract["latency_ms"] == 1600, "both attempts are counted"


# ---- reports ------------------------------------------------------------------


def document(paths, set_name="dev", freeze_record=None, replies=REPLIES):
    t, ledger, _ = transport(paths, FakeModel(replies))
    run_eval.run_models(MODELS, ITEMS, GOLD, DIRECTORY, SETTINGS, t, paths, announce=lambda *_: None)
    return run_eval.build_document(set_name, MODELS, ITEMS, GOLD, DIRECTORY, SETTINGS, ledger.as_dict(), paths, freeze_record)


def test_the_documents_carry_numbers_provenance_and_the_wrong_notes(paths, monkeypatch):
    monkeypatch.setattr(store, "label_path", lambda s: paths.results / "no-labels.jsonl")
    wrong = {**REPLIES, NOTES["n3"]: {"tasks": [one("Update the readme", deadline=TUE_END)]}}  # invents a deadline
    doc, errors = document(paths, replies=wrong)
    a = doc["models"]["model-a"]
    assert a["counts"]["items_scored"] == 4 and a["counts"]["items_with_a_gold_dependency"] == 1
    assert a["task_type_accuracy"]["k"] == 4 and a["json_valid_first_try"]["k"] == 4 and a["deadline_exact_match"]["k"] == 3
    assert [e["id"] for e in errors] == ["n3"] and errors[0]["categories"][0].startswith("deadline: none expected")
    prov = doc["provenance"]
    assert prov["n_scored"] == 4 and prov["n_with_dependency"] == 1 and prov["labeled_by"] == "human via eval/label_tool.py"
    assert prov["freeze_id"] is None and prov["ledger"]["api_calls_made"] == 8
    assert set(prov["prompt_sha256"]) >= {"extract.v1.md", "rewrite.v1.md", "match_team.v1.md"}


def test_written_reports_state_what_they_are_and_what_they_are_not(paths, monkeypatch):
    monkeypatch.setattr(store, "label_path", lambda s: paths.results / "no-labels.jsonl")
    doc, errors = document(paths)
    run_eval.write_reports(doc, errors, MODELS, paths)
    metrics = json.loads((paths.results / "metrics.json").read_text())
    assert metrics["set"] == "dev" and metrics["superseded"] is False and list(metrics["models"]) == MODELS
    summary = (paths.results / "summary.md").read_text()
    assert "NOT a reported result" in summary and "`model-a` | `model-b`" in summary
    assert "| Task type accuracy | 100.0%" in summary and "Wilson interval" in summary and "Limitations" in summary
    assert "written by hand by Jiya" in summary and "No label came from an AI" in summary
    assert "API calls actually made this session:** 8" in summary
    # the statements the lean design promises
    assert "Not evaluated:" in summary and "clarifying questions" in summary and "splitting a note" in summary
    assert "n = 4 notes gives wide intervals" in summary and "Routing rests on only 1 notes" in summary
    assert "Needs clarification" not in summary and "Task splitting" not in summary and "Task count" not in summary
    analysis = (paths.results / "ERROR_ANALYSIS.md").read_text()
    assert "Model analysed: `model-a`" in analysis and "0 of 4 notes" in analysis and "- None." in analysis


def test_the_test_set_summary_names_the_freeze(paths, monkeypatch):
    monkeypatch.setattr(store, "label_path", lambda s: paths.results / "no-labels.jsonl")
    freeze_record = {"freeze_id": "abc123def4", "git_commit": "0123456789abcdef", "prompt_sha256": {"extract.v1.md": "x"}}
    doc, errors = document(paths, "test", freeze_record)
    run_eval.write_reports(doc, errors, MODELS, paths)
    summary = (paths.results / "summary.md").read_text()
    assert summary.startswith("# Evaluation results") and "NOT a reported result" not in summary
    assert "freeze `abc123def4` at commit `0123456789`" in summary and "(version 2)" in summary


# ---- the estimate -------------------------------------------------------------


def estimate(paths, **kw):
    return run_eval.estimate(MODELS, ITEMS, GOLD, DIRECTORY, SETTINGS, paths.cache, **kw)


def test_the_estimate_uses_the_real_prompt_and_an_honest_guess_before_anything_is_cached(paths):
    est = estimate(paths)
    for v in est["per_model"].values():
        assert v["new_extract_calls"] == 4 and v["already_cached"] == 0 and v["completion_basis"].startswith("default guess")
        assert v["matcher_calls_at_most"] == 1  # only n1 has a gold dependency
        assert v["estimated_tokens"] > 4 * run_eval.DEFAULT_COMPLETION_TOKENS
    assert est["estimated_total_tokens"] == sum(v["estimated_tokens"] for v in est["per_model"].values())


def test_once_replies_are_cached_the_estimate_learns_from_them_and_skips_what_is_done(paths):
    run(paths)
    est = estimate(paths)
    v = est["per_model"]["model-a"]
    assert v["new_extract_calls"] == 0 and v["already_cached"] == 4
    assert v["completion_tokens_assumed"] == 400 and v["completion_basis"] == "average of cached replies"


def test_the_estimate_says_how_many_days_a_daily_cap_needs(paths):
    est = estimate(paths, daily_cap=6000)
    usable = int(6000 * 0.9)
    assert est["days_needed"] == math.ceil(est["estimated_total_tokens"] / usable) and est["days_needed"] > 1
    assert est["suggested_max_tokens_per_session"] == usable
    text = run_eval.format_estimate(est, "test")
    assert "needs about" in text and f"--max-tokens {usable}" in text and "an estimate, not a promise" in text
    assert "fits in one day" in run_eval.format_estimate(estimate(paths, daily_cap=10_000_000), "test")
    assert "--daily-token-cap" in run_eval.format_estimate(estimate(paths), "test")


# ---- commands and guards ------------------------------------------------------


def never_called(settings):
    raise AssertionError("the real API must not be touched")


def no_waiting():
    """A limiter with a budget so large it never sleeps, so command tests stay fast."""
    return cache.RateLimiter(10**12)


def args(**kw):
    base = dict(set="test", models=None, daily_token_cap=None, tpm=6500, max_tokens=None)
    return Namespace(**{**base, **kw})


def test_the_test_set_will_not_run_without_a_freeze(monkeypatch, capsys):
    monkeypatch.setattr(run_eval.freeze, "verify", lambda: ["there is no freeze yet: run python -m eval.freeze first"])
    monkeypatch.setattr(run_eval.freeze, "test_labels_ready", lambda: [])
    assert run_eval.cmd_run(args(), real_factory=never_called) == 2
    out = capsys.readouterr().out
    assert "Refusing to run the test set" in out and "no freeze yet" in out


def test_the_test_set_will_not_run_with_missing_labels_even_if_frozen(monkeypatch, capsys):
    monkeypatch.setattr(run_eval.freeze, "verify", lambda: [])
    monkeypatch.setattr(run_eval.freeze, "test_labels_ready", lambda: ["12 test note(s) have no label yet"])
    assert run_eval.cmd_run(args(), real_factory=never_called) == 2
    assert "12 test note(s) have no label yet" in capsys.readouterr().out


def test_report_for_the_test_set_is_guarded_too(monkeypatch):
    monkeypatch.setattr(run_eval.freeze, "verify", lambda: ["the test labels changed since the freeze"])
    monkeypatch.setattr(run_eval.freeze, "test_labels_ready", lambda: [])
    assert run_eval.cmd_report(args()) == 2


def test_the_dev_set_runs_end_to_end_through_the_command(monkeypatch, paths, capsys):
    monkeypatch.setattr(run_eval, "paths_for", lambda s: paths)
    monkeypatch.setattr(run_eval, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(run_eval, "load_gold", lambda s: {})
    fake = FakeModel()
    code = run_eval.cmd_run(args(set="dev", models="model-a"), real_factory=lambda settings: fake, limiter=no_waiting())
    out = capsys.readouterr().out
    n = len(store.load_inputs("dev"))
    assert code == 0 and "Estimate for the dev set" in out and f"API calls made: {n}" in out and "No labels yet" in out
    assert len(run_eval.read_predictions(paths, "model-a")) == n == 10
    assert not (paths.results / "metrics.json").exists(), "without gold labels there are no metrics to write"


def test_the_command_reports_an_incomplete_run_with_exit_code_3(monkeypatch, paths, capsys):
    monkeypatch.setattr(run_eval, "paths_for", lambda s: paths)
    monkeypatch.setattr(run_eval, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(run_eval, "load_gold", lambda s: {})
    code = run_eval.cmd_run(args(set="dev", models="model-a"), real_factory=lambda settings: FakeModel(fail_after=3), limiter=no_waiting())
    out = capsys.readouterr().out
    assert code == 3 and "Run incomplete" in out and "Run the same command again to resume" in out


def test_report_needs_complete_predictions(monkeypatch, paths, capsys):
    monkeypatch.setattr(run_eval, "paths_for", lambda s: paths)
    monkeypatch.setattr(run_eval, "get_settings", lambda: SETTINGS)
    assert run_eval.cmd_report(args(set="dev", models="model-a")) == 3
    assert "Predictions are incomplete" in capsys.readouterr().out


def test_status_prints_progress_and_freeze_state(capsys, monkeypatch):
    monkeypatch.setattr(run_eval.freeze, "read", lambda results_dir=None: None)
    assert run_eval.cmd_status(None) == 0
    out = capsys.readouterr().out
    assert "dev:" in out and "test:" in out and "freeze: not frozen yet" in out
