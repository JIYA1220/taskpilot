"""Turn metrics into the two markdown files. Only formatting: every number comes from metrics.json."""

from collections.abc import Callable

from eval import metrics as m


def pct(rate: dict | None) -> str:
    if not rate or rate.get("value") is None:
        return "n/a (nothing to measure)"
    low, high = rate["ci_low"] * 100, rate["ci_high"] * 100
    k = f" ({rate['k']}/{rate['n']})" if "k" in rate else f" (n={rate['n']})"
    return f"{rate['value'] * 100:.1f}% [{low:.1f}, {high:.1f}]{k}"


def num(stat: dict | None, unit: str = "", digits: int = 0) -> str:
    if not stat or stat.get("value") is None:
        return "n/a"
    ci = f" [{stat['ci_low']:.{digits}f}, {stat['ci_high']:.{digits}f}]" if stat.get("ci_low") is not None else ""
    return f"{stat['value']:.{digits}f}{unit}{ci} (n={stat['n']})"


def f1_cell(stat: dict) -> str:
    if stat.get("value") is None:
        return "n/a (no positive cases)"
    return f"{stat['value']:.3f} [{stat['ci_low']:.3f}, {stat['ci_high']:.3f}] (tp={stat['tp']}, fp={stat['fp']}, fn={stat['fn']})"


ROWS: list[tuple[str, Callable[[dict], str]]] = [
    ("**Extraction**", lambda x: ""),
    ("JSON valid on first try", lambda x: pct(x["json_valid_first_try"])),
    ("JSON valid after the one retry", lambda x: pct(x["json_valid_after_retry"])),
    ("Task type accuracy", lambda x: pct(x["task_type_accuracy"])),
    ("Deadline exact match", lambda x: pct(x["deadline_exact_match"])),
    ("**Dependency detection**", lambda x: ""),
    ("Precision", lambda x: pct(x["dependency"]["precision"])),
    ("Recall", lambda x: pct(x["dependency"]["recall"])),
    ("F1", lambda x: f1_cell(x["dependency"]["f1"])),
    ("Direction correct, among true positives", lambda x: pct(x["dependency"]["direction_accuracy_on_true_positives"])),
    ("**Team routing, top-1** (notes whose gold names a directory team)", lambda x: ""),
    ("Keyword only", lambda x: pct(x["team_routing"]["keyword_only"])),
    ("Extract step's own pick", lambda x: pct(x["team_routing"]["step_a_pick"])),
    ("Keyword + model (the product's matcher)", lambda x: pct(x["team_routing"]["keyword_plus_model"])),
    ("Keywords were decisive on their own", lambda x: pct(x["team_routing"]["keyword_decisive_rate"])),
    ("Model had to be asked", lambda x: pct(x["team_routing"]["model_called_rate"])),
    ("**Cost**", lambda x: ""),
    ("Latency of the extract call, median (ms)", lambda x: num(x["latency_ms"]["extract_median"])),
    ("Latency of the extract call, p95 (ms)", lambda x: num(x["latency_ms"]["extract_p95"])),
    ("Latency of the matcher call when the model was asked, median (ms)", lambda x: num(x["latency_ms"]["matcher_median_when_model_called"])),
    ("Mean tokens per note (input + output)", lambda x: num(x["tokens"]["mean_total_per_note"], digits=0)),
    ("Mean reasoning tokens per note (part of the output)", lambda x: num(x["tokens"]["mean_reasoning_per_note"], digits=0)),
]


def render_summary(doc: dict) -> str:
    models = list(doc["models"])
    prov, final = doc["provenance"], doc["set"] == "test"
    title = "Evaluation results" if final else "Development set run (used for prompt tuning: NOT a reported result)"
    lines = [f"# {title}", ""]
    if not final:
        lines += ["> Dev results guide prompt changes only. The reported numbers come from the frozen test run.", ""]
    lines += [
        "## What was measured",
        "",
        f"- **Notes:** {prov['n_scored']} scored, all single-task, from `{prov['input_file']}` ({prov['n_with_dependency']} of them have a dependency on another team).",
        gold_line(prov),
        "- **Not evaluated:** the clarifying questions the app asks, and splitting a note into several tasks. Both exist in the product; neither is measured here.",
        "- **Notes:** written by an AI assistant (not by the evaluated models) after the prompts were fixed.",
        f"- **Models:** {', '.join(f'`{x}`' for x in models)}, each doing every step (extraction and the team matcher), temperature 0, reasoning effort `{prov['reasoning_effort']}`.",
        f"- **Fixed reference time:** {prov['fixed_now']} (all relative dates are resolved against it).",
        f"- **Prompt versions:** {', '.join(f'`{k}`' for k in sorted(prov['prompt_sha256']))} (sha256 recorded in `metrics.json`).",
    ]
    if final:
        lines.append(f"- **Frozen:** freeze `{prov['freeze_id']}` at commit `{prov['git_commit'][:10]}`; prompts, matcher, scoring code, directory, notes and labels were committed and verified unchanged before this run.")
    lines += [
        f"- **API calls actually made this session:** {prov['ledger']['api_calls_made']}; cache hits: {prov['ledger']['cache_hits']} "
        f"(hit rate {pct_value(prov['ledger']['cache_hit_rate'])}); tokens spent by new calls: {prov['ledger']['tokens_spent_by_new_calls']}.",
        f"- **Generated:** {prov['generated_at']}.",
        "",
        "## Results",
        "",
        "Every rate shows the value, a 95% interval and the counts. Proportions use the Wilson interval; F1, medians, p95 and means use a percentile bootstrap over notes (2000 resamples).",
        "",
        "| Metric | " + " | ".join(f"`{x}`" for x in models) + " |",
        "|---|" + "---|" * len(models),
    ]
    for label, cell in ROWS:
        lines.append(f"| {label} | " + " | ".join(cell(doc['models'][x]) for x in models) + " |")
    first = doc["models"][models[0]]
    n, routed = first["counts"]["items_scored"], first["team_routing"]["n_items_with_a_gold_team"]
    lines += [
        "",
        "## How to read this",
        "",
        f"- **n = {n} notes gives wide intervals.** A rate of 80% on 30 notes is compatible with a true rate anywhere from the high 60s to the low 90s. Differences of a few notes between the models are within noise.",
        f"- **Routing rests on only {routed} notes** (those whose gold names a team), so its intervals are wider still; read the three routing rows as a rough comparison, not a ranking.",
        "- If the model returns several tasks for a note, its first task is the one compared with the gold label. Splitting is not evaluated.",
        "- Overlapping intervals between the two models are not evidence that they are equal, and non-overlapping ones are not a formal significance test.",
        "- Keyword-only routing answers with the highest-scoring team; a tie or no evidence counts as wrong.",
        "- Latency is the recorded time of the original API call (cached reruns reuse it). Output tokens include the models' hidden reasoning tokens.",
        "",
        "## Limitations",
        "",
        "- The evaluation is deliberately lean: clarifying questions and task splitting are not evaluated, and only three properties of each note are scored.",
        *limitation_on_labels(prov),
        "- The demo organization, tasks and directory are synthetic.",
        "- The two models are two sizes of one open-weight family, and the result is for one prompt version at one time.",
        "- The test notes were written by the same assistant that wrote the prompts. The prompts were not tuned on the test notes, but the wording of the notes may still resemble the prompts.",
        "",
    ]
    return "\n".join(lines)


def limitation_on_labels(prov: dict) -> list[str]:
    if prov.get("gold_source") == "ai":
        return [
            "- **The notes, the prompts and the gold labels were all written by the same AI assistant.** Shared blind spots are possible and agreement may be inflated; "
            "the evaluated models are a different, open-weight family. The labels were written from the guide before any model ran on these notes and were not adjusted afterwards.",
            "- Because no human wrote the labels, label noise from a human labeler is not measured, and this result says nothing about how a human would label these notes.",
        ]
    return ["- One person labeled every note, so label noise is not measured."]


def gold_line(prov: dict) -> str:
    """Who wrote the gold labels, stated exactly as recorded in the label files."""
    source = prov.get("gold_source", "human")
    if source == "ai":
        return (
            "- **Gold labels:** three per note (task type, dependency, deadline), **written by the AI assistant (Claude) applying "
            "`eval/LABELING_GUIDE.md` (version 2) to each note. They were NOT written by a human.** This measures how well the "
            "models agree with the guide as an AI reads it. It is **not** a human-labeled benchmark and must not be described as one."
        )
    if source == "mixed":
        return "- **Gold labels:** three per note. **Some were written by a human and some by the AI assistant**; see `labeled_by` in `metrics.json`. Do not describe this as human-labeled."
    return (
        "- **Gold labels:** three per note (task type, dependency, deadline), written by hand by Jiya with `eval/label_tool.py`, "
        "blind to any model output, following `eval/LABELING_GUIDE.md` (version 2). No label came from an AI."
    )


def pct_value(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render_error_analysis(model: str, set_name: str, n_scored: int, errors: list[dict]) -> str:
    counts = m.category_counts(errors)
    lines = [
        "# Error analysis" + ("" if set_name == "test" else " (development set; used for tuning)"),
        "",
        f"Model analysed: `{model}`. {len(errors)} of {n_scored} notes had at least one wrong field. Counts and facts only, no explanations.",
        "",
        "## Counts by category",
        "",
        "| Category | Occurrences |",
        "|---|---|",
    ]
    lines += [f"| {category} | {count} |" for category, count in counts.items()] or ["| (none) | 0 |"]
    lines += ["", "## Every wrong note", ""]
    for e in errors:
        lines.append(f"- `{e['id']}` \"{e['note']}\"")
        lines += [f"  - {c}" for c in e["categories"]]
    if not errors:
        lines.append("- None.")
    return "\n".join(lines) + "\n"
