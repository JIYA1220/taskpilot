"""Freeze the evaluation before the final run.

    python -m eval.freeze          create the freeze (everything must be committed first)
    python -m eval.freeze --check  verify nothing has changed since

The point: the test notes must not influence the prompts, and the scoring must not be adjusted
after the labels exist. A freeze records the exact committed content of everything the result
depends on. `run_eval run --set test` refuses to start unless the freeze still matches, so a
change to a prompt, the matcher, the directory, the test notes, the labels or the metrics code
forces a new freeze, and the old results are kept but marked superseded.
"""

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from backend.config import get_settings
from eval import labels as store

ROOT = store.EVAL_DIR.parent
RESULTS = store.EVAL_DIR / "results"

# Directories and files whose committed content defines a result.
FROZEN = [
    "backend/prompts",
    "backend/organize",
    "backend/conflicts",
    "backend/llm.py",
    "backend/directory.py",
    "backend/config.py",
    "eval/directory.json",
    "eval/dataset.jsonl",
    "eval/labels/dataset.labels.jsonl",
    "eval/LABELING_GUIDE.md",
    "eval/metrics.py",
    "eval/stats.py",
    "eval/predict.py",
    "eval/cache.py",
    "eval/run_eval.py",
]
RESULT_FILES = ["metrics.json", "summary.md", "ERROR_ANALYSIS.md", "predictions"]


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"git {' '.join(args)} failed")
    # Only the trailing newline goes: `git status --porcelain` lines start with a space that matters.
    return done.stdout.rstrip("\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path = ROOT, paths: list[str] = FROZEN) -> dict:
    """The committed identity of every frozen path (git tree or blob id), and what is uncommitted."""
    return {
        "git_commit": _git(root, "rev-parse", "HEAD"),
        "git_ids": {p: _git(root, "rev-parse", f"HEAD:{p}") for p in paths},
        "uncommitted": [line for line in _git(root, "status", "--porcelain", "--", *paths).splitlines() if line],
    }


def test_labels_ready() -> list[str]:
    """Reasons the test labels are not ready (empty list means ready)."""
    progress = store.progress("test")
    problems = []
    if progress.remaining:
        problems.append(f"{progress.remaining} test note(s) have no label yet")
    if progress.flagged:
        problems.append(f"{progress.flagged} test note(s) are flagged as ambiguous and need a rewrite and a label")
    return problems


def create(root: Path = ROOT, results_dir: Path = RESULTS, paths: list[str] = FROZEN, require_labels: bool = True) -> dict:
    problems = test_labels_ready() if require_labels else []
    snap = snapshot(root, paths)
    if snap["uncommitted"]:
        problems.append("uncommitted changes in frozen files (commit them first): " + ", ".join(line[3:] for line in snap["uncommitted"]))
    if problems:
        raise RuntimeError("cannot freeze: " + "; ".join(problems))

    settings = get_settings()
    prompts = {p.name: _sha256(p) for p in sorted((root / "backend" / "prompts").glob("*.md"))}
    record = {
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": snap["git_commit"],
        "git_ids": snap["git_ids"],
        "prompt_sha256": prompts,
        "models": {"main": settings.groq_main_model, "fast": settings.groq_fast_model, "reasoning_effort": settings.groq_reasoning_effort},
        "fixed_now": store.FIXED_NOW.isoformat(),
        "test_inputs_sha256": _sha256(store.input_path("test")),
        "test_labels_sha256": _sha256(store.label_path("test")) if store.label_path("test").exists() else None,
        "n_test_items": len(store.load_inputs("test")),
    }
    record["freeze_id"] = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:10]
    results_dir.mkdir(parents=True, exist_ok=True)
    previous = read(results_dir)
    if previous:
        supersede(results_dir, previous["freeze_id"])
    (results_dir / "freeze.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def read(results_dir: Path = RESULTS) -> dict | None:
    path = results_dir / "freeze.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def verify(root: Path = ROOT, results_dir: Path = RESULTS, paths: list[str] = FROZEN) -> list[str]:
    """Why the final run must not start (empty list means the freeze still holds)."""
    record = read(results_dir)
    if record is None:
        return ["there is no freeze yet: run python -m eval.freeze first"]
    problems = []
    snap = snapshot(root, paths)
    for path, ident in record["git_ids"].items():
        if snap["git_ids"].get(path) != ident:
            problems.append(f"{path} changed since the freeze")
    if snap["uncommitted"]:
        problems.append("uncommitted changes in frozen files: " + ", ".join(line[3:] for line in snap["uncommitted"]))
    if _sha256(store.input_path("test")) != record["test_inputs_sha256"]:
        problems.append("the test notes changed since the freeze")
    labels = store.label_path("test")
    if not labels.exists() or _sha256(labels) != record["test_labels_sha256"]:
        problems.append("the test labels changed since the freeze")
    return problems


def supersede(results_dir: Path, freeze_id: str) -> Path | None:
    """Keep earlier results, clearly marked, when a new freeze replaces them."""
    present = [results_dir / name for name in RESULT_FILES + ["freeze.json"] if (results_dir / name).exists()]
    if not present:
        return None
    target = results_dir / "superseded" / freeze_id
    target.mkdir(parents=True, exist_ok=True)
    for item in present:
        shutil.move(str(item), str(target / item.name))
    (target / "SUPERSEDED.md").write_text(
        f"These results belong to freeze `{freeze_id}` and are **superseded**: something the result depends on "
        "(a prompt, the code, the labels or the scoring) changed and the evaluation was frozen and run again. "
        "They are kept for the record and must not be quoted as current results.\n",
        encoding="utf-8",
    )
    return target


def main(argv: list[str]) -> int:
    if argv == ["--check"]:
        problems = verify()
        print("Freeze holds." if not problems else "Freeze broken:\n - " + "\n - ".join(problems))
        return 0 if not problems else 1
    if argv:
        print(__doc__)
        return 2
    try:
        record = create()
    except RuntimeError as exc:
        print(exc)
        return 1
    print(f"Frozen as {record['freeze_id']} at commit {record['git_commit'][:10]}. Commit eval/results/freeze.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
