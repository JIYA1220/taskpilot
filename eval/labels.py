"""Gold-label storage and validation for the evaluation sets (the lean design).

Every note is a single task, and a label has exactly three fields:
    task_type   "normal" or "email"
    dependency  null, or {"team": <directory team id>, "direction": "need_from_them" | "they_need_from_me"}
    deadline    null, or an ISO 8601 time with offset, after the fixed reference time

Labels live in append-only JSONL files under eval/labels/. A record is only ever written by
eval/label_tool.py from a person's answers; nothing here calls a model.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

EVAL_DIR = Path(__file__).resolve().parent
LABEL_DIR = EVAL_DIR / "labels"

TZ = ZoneInfo("Asia/Kolkata")
# Every note is read as if written at this moment (see LABELING_GUIDE.md).
FIXED_NOW = datetime(2026, 11, 2, 9, 0, tzinfo=TZ)

TASK_TYPES = ("normal", "email")
DIRECTIONS = ("need_from_them", "they_need_from_me")
OWN_TEAM = "product"  # the person labeling is on this team; a dependency is always on another team
LABELED_BY = "human via eval/label_tool.py"
# Used only for a set whose gold labels were written by the assistant, by the project owner's instruction.
# Reports read this marker, so such a set can never be presented as human-labeled.
AI_LABELED_BY = "AI assistant (Claude) applying LABELING_GUIDE.md v2; NOT written by a human"
LABEL_FIELDS = ("task_type", "dependency", "deadline")

SETS = {"dev": "dev.jsonl", "test": "dataset.jsonl"}


def input_path(set_name: str) -> Path:
    return EVAL_DIR / SETS[set_name]


def label_path(set_name: str) -> Path:
    return LABEL_DIR / f"{Path(SETS[set_name]).stem}.labels.jsonl"


def load_inputs(set_name: str) -> list[dict]:
    lines = input_path(set_name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def read_history(set_name: str) -> list[dict]:
    path = label_path(set_name)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def latest_by_id(history: list[dict]) -> dict[str, dict]:
    """The most recent record per item wins; earlier ones stay in the file as history."""
    latest: dict[str, dict] = {}
    for record in history:
        latest[record["id"]] = record
    return latest


def append_record(set_name: str, record: dict, labeled_by: str = LABELED_BY) -> dict:
    LABEL_DIR.mkdir(parents=True, exist_ok=True)
    previous = [r for r in read_history(set_name) if r["id"] == record["id"]]
    record = {
        **record,
        "labeled_at": datetime.now(UTC).isoformat(),
        "labeled_by": labeled_by,
        "revision": len(previous) + 1,
    }
    with label_path(set_name).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def parse_deadline(text: str) -> str | None:
    """'YYYY-MM-DD HH:MM' in IST -> ISO 8601 with offset. Blank means no deadline.
    Raises ValueError with a message meant for the person labeling."""
    text = text.strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("use YYYY-MM-DD HH:MM, for example 2026-11-03 17:00") from None
    value = parsed.replace(tzinfo=TZ)
    if value <= FIXED_NOW:
        raise ValueError("a deadline must be after Mon 2 Nov 2026 09:00 (the reference time)")
    return value.isoformat()


def validate_labels(labels: dict, team_ids: list[str]) -> list[str]:
    """Return a list of problems (empty means the labels are consistent)."""
    problems = []
    extra = set(labels) - set(LABEL_FIELDS)
    if extra:
        problems.append(f"unexpected field(s) {sorted(extra)}: a label has exactly {list(LABEL_FIELDS)}")
    missing = set(LABEL_FIELDS) - set(labels)
    if missing:
        return problems + [f"missing field(s) {sorted(missing)}"]

    if labels["task_type"] not in TASK_TYPES:
        problems.append(f"task_type must be one of {TASK_TYPES}")

    dependency = labels["dependency"]
    if dependency is not None:
        if not isinstance(dependency, dict) or set(dependency) != {"team", "direction"}:
            problems.append("dependency must be null or {team, direction}")
        else:
            if dependency["direction"] not in DIRECTIONS:
                problems.append(f"dependency direction must be one of {DIRECTIONS}")
            if dependency["team"] == OWN_TEAM:
                problems.append("a dependency is on another team, never your own (rule D2)")
            elif dependency["team"] not in team_ids:
                problems.append("dependency team must be a directory team id")

    deadline = labels["deadline"]
    if deadline is not None:
        try:
            value = datetime.fromisoformat(deadline)
        except (TypeError, ValueError):
            problems.append("deadline must be ISO 8601 or null")
        else:
            if value.tzinfo is None or value <= FIXED_NOW:
                problems.append("deadline must include an offset and be after the reference time")
    return problems


@dataclass
class Progress:
    total: int
    labeled: int
    flagged: int

    @property
    def remaining(self) -> int:
        return self.total - self.labeled - self.flagged


def progress(set_name: str) -> Progress:
    latest = latest_by_id(read_history(set_name))
    inputs = load_inputs(set_name)
    statuses = [latest[i["id"]]["status"] for i in inputs if i["id"] in latest]
    return Progress(len(inputs), statuses.count("labeled"), statuses.count("flagged"))
