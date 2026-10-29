"""Run the evaluation.

    python -m eval.run_eval status
    python -m eval.run_eval estimate --set test --daily-token-cap 200000
    python -m eval.run_eval run --set dev                 # free to run while tuning prompts
    python -m eval.run_eval run --set test                # the final run: only after `python -m eval.freeze`
    python -m eval.run_eval report --set dev              # rebuild reports from saved predictions, no API calls

Safeguards:
  * `run --set test` refuses unless the freeze still holds and every test note is labeled.
  * Every model reply is cached (eval/cache/, ignored by git), so a stopped run resumes where it
    stopped and a rerun costs nothing.
  * A rate-limit, quota or network error stops the run cleanly (exit code 3). It is never scored
    as a wrong answer.
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backend.config import Settings, get_settings
from backend.directory import Directory, load_directory
from backend.llm import Transport, groq_transport, load_prompt
from backend.organize.steps import build_extract_message
from eval import cache, freeze, metrics, predict, report
from eval import labels as store

RESULTS = store.EVAL_DIR / "results"
CACHE_DIR = store.EVAL_DIR / "cache"
DEFAULT_TPM = 6500  # stay under Groq's 8,000 tokens per minute with room for the estimate to be wrong
PROMPT_CHARS_PER_TOKEN = 3.5
DEFAULT_COMPLETION_TOKENS = 1000  # a typical reply including hidden reasoning, until real numbers are cached
MATCHER_CALL_TOKENS = 1100


@dataclass
class Paths:
    results: Path
    cache: Path

    @property
    def predictions(self) -> Path:
        return self.results / "predictions"


def paths_for(set_name: str) -> Paths:
    return Paths(RESULTS if set_name == "test" else RESULTS / "dev", CACHE_DIR)


def load_gold(set_name: str) -> dict[str, dict]:
    latest = store.latest_by_id(store.read_history(set_name))
    return {i: r for i, r in latest.items() if r["status"] == "labeled"}


# ---- running ------------------------------------------------------------------


def prediction_file(paths: Paths, model: str) -> Path:
    return paths.predictions / f"{cache.slug(model)}.jsonl"


def read_predictions(paths: Paths, model: str) -> dict[str, dict]:
    path = prediction_file(paths, model)
    if not path.exists():
        return {}
    rows = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return {r["id"]: r for r in rows}


def run_models(
    models: list[str],
    items: list[dict],
    gold: dict[str, dict],
    directory: Directory,
    settings: Settings,
    transport: Transport,
    paths: Paths,
    announce=print,
) -> str | None:
    """Predict every note with every model. Returns None when finished, or the reason it stopped."""
    paths.predictions.mkdir(parents=True, exist_ok=True)
    for model in models:
        model_settings = predict.settings_for(settings, model)
        records: dict[str, dict] = {}
        for index, item in enumerate(items, 1):
            try:
                record = predict.predict_item(transport, model_settings, directory, item["id"], item["input"], gold.get(item["id"]))
            except cache.EvalInfraError as exc:
                prediction_file(paths, model).write_text("".join(json.dumps(r) + "\n" for r in records.values()), encoding="utf-8")
                return f"stopped on {model} after {len(records)} of {len(items)} notes: {exc}"
            record["model"] = model
            records[item["id"]] = record
            announce(f"[{model}] {index}/{len(items)} {item['id']}")
        prediction_file(paths, model).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records.values()), encoding="utf-8")
    return None


# ---- reporting ----------------------------------------------------------------


def build_document(
    set_name: str, models: list[str], items: list[dict], gold: dict[str, dict], directory: Directory,
    settings: Settings, ledger: dict, paths: Paths, freeze_record: dict | None,
) -> tuple[dict, list[dict]]:
    team_ids = directory.team_ids()
    notes = {i["id"]: i["input"] for i in items}
    per_model, first_errors = {}, []
    for index, model in enumerate(models):
        predictions = read_predictions(paths, model)
        scored_gold = {i: g for i, g in gold.items() if i in notes}
        per_model[model] = metrics.score(scored_gold, predictions, team_ids)
        if index == 0:  # the error analysis is for the main model
            first_errors = metrics.error_items(scored_gold, predictions, notes, team_ids)
    first = per_model[models[0]]["counts"]
    provenance = {
        "input_file": store.SETS[set_name],
        "n_scored": first["items_scored"],
        "n_with_dependency": first["items_with_a_gold_dependency"],
        "reasoning_effort": settings.groq_reasoning_effort,
        "fixed_now": store.FIXED_NOW.isoformat(),
        "prompt_sha256": freeze_record["prompt_sha256"] if freeze_record else {p.name: freeze._sha256(p) for p in sorted((Path(__file__).resolve().parent.parent / "backend" / "prompts").glob("*.md"))},
        "freeze_id": freeze_record["freeze_id"] if freeze_record else None,
        "git_commit": freeze_record["git_commit"] if freeze_record else None,
        "labels_sha256": freeze._sha256(store.label_path(set_name)) if store.label_path(set_name).exists() else None,
        "ledger": ledger,
        "generated_at": datetime.now(UTC).isoformat(),
        "labeled_by": "human via eval/label_tool.py",
    }
    return {"set": set_name, "superseded": False, "provenance": provenance, "models": per_model}, first_errors


def write_reports(document: dict, errors: list[dict], models: list[str], paths: Paths) -> None:
    paths.results.mkdir(parents=True, exist_ok=True)
    (paths.results / "metrics.json").write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (paths.results / "summary.md").write_text(report.render_summary(document), encoding="utf-8")
    scored = document["provenance"]["n_scored"]
    (paths.results / "ERROR_ANALYSIS.md").write_text(report.render_error_analysis(models[0], document["set"], scored, errors), encoding="utf-8")


# ---- estimating ---------------------------------------------------------------


def estimate(
    models: list[str], items: list[dict], gold: dict[str, dict], directory: Directory, settings: Settings,
    cache_dir: Path, daily_cap: int | None = None, tpm: int = DEFAULT_TPM,
) -> dict:
    """How many tokens, calls and minutes a run needs, using the app's real prompt and any real
    reply sizes already in the cache. An estimate, not a promise: say so when reporting it."""
    system = load_prompt("extract").system
    probe = cache.CachingTransport(lambda *a: None, cache_dir, settings.groq_reasoning_effort, cache.Ledger())
    deps = sum(1 for g in gold.values() if g["dependency"] and g["dependency"]["team"] in directory.team_ids())
    matcher_calls = deps if gold else math.ceil(0.3 * len(items))
    per_model = {}
    for model in models:
        s = predict.settings_for(settings, model)
        probe._load(model)
        outs = [r["tokens_out"] for r in probe._memory.values() if r["model"] == model]
        completion = sum(outs) / len(outs) if outs else DEFAULT_COMPLETION_TOKENS
        new_calls, prompt_tokens = 0, 0
        for item in items:
            user = build_extract_message(s, directory, predict.OWN_TEAM, item["input"], store.FIXED_NOW)
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            if cache.cache_key(model, messages, 2000, settings.groq_reasoning_effort) not in probe._memory:
                new_calls += 1
                prompt_tokens += (len(system) + len(user)) / PROMPT_CHARS_PER_TOKEN
        tokens = prompt_tokens + new_calls * completion + matcher_calls * MATCHER_CALL_TOKENS
        per_model[model] = {
            "new_extract_calls": new_calls,
            "already_cached": len(items) - new_calls,
            "completion_tokens_assumed": round(completion),
            "completion_basis": "average of cached replies" if outs else "default guess (no cached replies yet)",
            "matcher_calls_at_most": matcher_calls,
            "estimated_tokens": round(tokens),
        }
    total = sum(v["estimated_tokens"] for v in per_model.values())
    calls = sum(v["new_extract_calls"] + v["matcher_calls_at_most"] for v in per_model.values())
    out = {
        "per_model": per_model,
        "estimated_total_tokens": total,
        "estimated_api_calls_at_most": calls,
        "estimated_minutes_at_the_rate_limit": round(total / tpm, 1),
        "tokens_per_minute_used": tpm,
    }
    if daily_cap:
        usable = int(daily_cap * 0.9)  # leave headroom: the estimate can be wrong
        out["daily_token_cap"] = daily_cap
        out["days_needed"] = max(1, math.ceil(total / usable))
        out["suggested_max_tokens_per_session"] = usable
    return out


def format_estimate(est: dict, set_name: str) -> str:
    lines = [f"Estimate for the {set_name} set (this is an estimate, not a promise):"]
    for model, v in est["per_model"].items():
        lines.append(
            f"  {model}: {v['new_extract_calls']} new extraction calls ({v['already_cached']} already cached), up to {v['matcher_calls_at_most']} matcher calls, "
            f"about {v['estimated_tokens']:,} tokens (replies assumed {v['completion_tokens_assumed']} tokens: {v['completion_basis']})"
        )
    lines.append(f"  Total: about {est['estimated_total_tokens']:,} tokens in at most {est['estimated_api_calls_at_most']} calls, roughly {est['estimated_minutes_at_the_rate_limit']} minutes at the rate limit.")
    if "days_needed" in est:
        verdict = "fits in one day" if est["days_needed"] == 1 else f"needs about {est['days_needed']} days: run with --max-tokens {est['suggested_max_tokens_per_session']} each day and rerun to resume"
        lines.append(f"  Against your daily cap of {est['daily_token_cap']:,} tokens, this {verdict}.")
    else:
        lines.append("  Add --daily-token-cap N (from the Groq console) to see whether it fits in a day.")
    return "\n".join(lines)


# ---- commands -----------------------------------------------------------------


def _models(args, settings: Settings) -> list[str]:
    return args.models.split(",") if args.models else [settings.groq_main_model, settings.groq_fast_model]


def _refusals(set_name: str) -> list[str]:
    if set_name != "test":
        return []
    return freeze.verify() + freeze.test_labels_ready()


def cmd_status(_args) -> int:
    for set_name in ("dev", "test"):
        p = store.progress(set_name)
        print(f"{set_name}: {p.labeled} labeled, {p.flagged} flagged, {p.remaining} unlabeled of {p.total}")
    record = freeze.read()
    problems = freeze.verify() if record else ["not frozen yet"]
    print("freeze:", f"{record['freeze_id']} holds" if record and not problems else "; ".join(problems))
    return 0


def cmd_estimate(args) -> int:
    settings, directory = get_settings(), None
    directory = load_directory(settings.directory_path)
    est = estimate(_models(args, settings), store.load_inputs(args.set), load_gold(args.set), directory, settings, CACHE_DIR, args.daily_token_cap, args.tpm)
    print(format_estimate(est, args.set))
    return 0


def cmd_run(args, real_factory=groq_transport, limiter: cache.RateLimiter | None = None) -> int:
    refusals = _refusals(args.set)
    if refusals:
        print("Refusing to run the test set:\n - " + "\n - ".join(refusals))
        return 2
    settings = get_settings()
    directory = load_directory(settings.directory_path)
    models, items, gold, paths = _models(args, settings), store.load_inputs(args.set), load_gold(args.set), paths_for(args.set)
    print(format_estimate(estimate(models, items, gold, directory, settings, paths.cache, args.daily_token_cap, args.tpm), args.set))

    ledger = cache.Ledger()
    transport = cache.CachingTransport(
        real_factory(settings), paths.cache, settings.groq_reasoning_effort, ledger,
        limiter=limiter or cache.RateLimiter(args.tpm), token_budget=args.max_tokens,
    )
    stopped = run_models(models, items, gold, directory, settings, transport, paths)
    print(f"API calls made: {ledger.api_calls}, cache hits: {ledger.cache_hits}, tokens spent: {ledger.tokens_spent}")
    if stopped:
        print(f"Run incomplete, {stopped}. Run the same command again to resume.")
        return 3
    return _finish(args.set, models, items, gold, directory, settings, ledger.as_dict(), paths)


def _finish(set_name, models, items, gold, directory, settings, ledger, paths) -> int:
    if not gold:
        print("No labels yet for this set: predictions are saved, but metrics need gold labels (python -m eval.label_tool).")
        return 0
    document, errors = build_document(set_name, models, items, gold, directory, settings, ledger, paths, freeze.read() if set_name == "test" else None)
    write_reports(document, errors, models, paths)
    print(f"Wrote {paths.results / 'metrics.json'}, summary.md and ERROR_ANALYSIS.md")
    return 0


def cmd_report(args) -> int:
    refusals = _refusals(args.set)
    if refusals:
        print("Refusing:\n - " + "\n - ".join(refusals))
        return 2
    settings = get_settings()
    directory = load_directory(settings.directory_path)
    models, items, gold, paths = _models(args, settings), store.load_inputs(args.set), load_gold(args.set), paths_for(args.set)
    missing = [m for m in models if len(read_predictions(paths, m)) < len(items)]
    if missing:
        print("Predictions are incomplete for: " + ", ".join(missing) + ". Run `run` first.")
        return 3
    return _finish(args.set, models, items, gold, directory, settings, cache.Ledger().as_dict() | {"note": "report only, no API calls"}, paths)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    for name in ("estimate", "run", "report"):
        p = sub.add_parser(name)
        p.add_argument("--set", choices=sorted(store.SETS), required=True)
        p.add_argument("--models", help="comma-separated; default: the main and fast model from .env")
        p.add_argument("--daily-token-cap", type=int, help="tokens per day from the Groq console")
        p.add_argument("--tpm", type=int, default=DEFAULT_TPM, help="tokens per minute to stay under")
        p.add_argument("--max-tokens", type=int, help="stop after spending this many tokens this session")
    args = parser.parse_args(argv)
    return {"status": cmd_status, "estimate": cmd_estimate, "run": cmd_run, "report": cmd_report}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
