"""Generate eval/dev.jsonl, eval/dataset.jsonl and eval/design/coverage.json (the lean evaluation).

    python -m eval.design.build_inputs

The notes below were written by Claude (an AI assistant), not by the evaluated models.
There are deliberately NO gold labels in this file: labels come only from a person via
eval/label_tool.py. Each note is tagged only with the design category it was written to cover
(for example "dep_need+deadline"), kept out of the input files so neither the labeling tool nor
the labeler ever sees it. Notes are shuffled with a fixed seed and given opaque ids.

Lean design: every note is a single task.
  test (30): 10 notes with a dependency on another team (7 needing something, 3 reverse),
             8 email tasks, 12 others (7 plain, 5 hard negatives), 10 notes with a relative deadline.
  dev  (10): 3 dependency notes, 2 email tasks, 5 others.
"""

import json
import random
from pathlib import Path

from eval.design import ai_gold

DESIGN_DIR = Path(__file__).resolve().parent
EVAL_DIR = DESIGN_DIR.parent
SEED = 20261030  # round 2 keeps the dev seed; the test seed is SEED + 1

DEV = [
    ("dep_need", "need the settlements dashboard export for my report"),
    ("dep_need+deadline", "ask the platform team to restart the ci/cd runner by 5"),
    ("dep_reverse", "sneha is waiting for my auth service test results"),
    ("email", "send ravi the meeting notes"),
    ("email+deadline", "mail arjun the api questions by 5"),
    ("plain", "clean up unused styles in the web app"),
    ("plain", "write the release notes for version 2"),
    ("plain+deadline", "prepare the demo slides by friday"),
    ("plain+deadline", "fix the typo in the footer tomorrow"),
    ("trap", "call the courier about the delayed package"),
]

# Round 2: the test notes live next to their AI-written gold labels in eval/design/ai_gold.py.
TEST = [(row[0], row[1]) for row in ai_gold.TEST_GOLD]


def build(items: list[tuple[str, str]], prefix: str, seed: int) -> tuple[list[dict], dict[str, str]]:
    shuffled = items[:]
    random.Random(seed).shuffle(shuffled)
    rows, coverage = [], {}
    for n, (category, text) in enumerate(shuffled, 1):
        item_id = f"{prefix}{n:02d}"
        rows.append({"id": item_id, "input": text})
        coverage[item_id] = category
    return rows, coverage


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def main() -> None:
    dev_rows, dev_cov = build(DEV, "d", SEED)
    test_rows, test_cov = build(TEST, "t", SEED + 1)
    write_jsonl(EVAL_DIR / "dev.jsonl", dev_rows)
    write_jsonl(EVAL_DIR / "dataset.jsonl", test_rows)
    (DESIGN_DIR / "coverage.json").write_text(json.dumps({"dev": dev_cov, "test": test_cov}, indent=1) + "\n", encoding="utf-8")
    print(f"dev: {len(dev_rows)} notes, test: {len(test_rows)} notes")


if __name__ == "__main__":
    main()
