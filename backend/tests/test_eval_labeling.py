import ast
import json
from collections import Counter
from pathlib import Path

import pytest

from eval import label_tool, labels as store

EVAL = Path(store.EVAL_DIR)
TEAMS = ["product", "payments", "platform", "data"]
COVERAGE = json.loads((EVAL / "design" / "coverage.json").read_text(encoding="utf-8"))


# ---- the input sets --------------------------------------------------------


def test_set_sizes_and_shape():
    dev, test = store.load_inputs("dev"), store.load_inputs("test")
    assert (len(dev), len(test)) == (15, 60)
    for rows in (dev, test):
        assert all(set(r) == {"id", "input"} for r in rows), "input files hold only id and text, no labels or categories"
        assert all(r["input"].strip() for r in rows)
        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids))


def test_test_set_has_50_single_and_10_multi_task_notes():
    counts = Counter(COVERAGE["test"].values())
    assert counts["multi"] == 10
    assert sum(v for k, v in counts.items() if k != "multi") == 50
    assert counts == {
        "normal_plain": 6, "normal_deadline": 9, "email": 7, "dep_explicit": 5, "dep_implicit": 5,
        "dep_reverse": 6, "vague": 7, "trap": 5, "multi": 10,
    }


def test_dev_set_covers_every_kind_including_multi_task():
    kinds = set(COVERAGE["dev"].values())
    assert {"email", "dep_explicit", "dep_implicit", "dep_reverse", "vague", "trap", "multi", "normal_deadline"} <= kinds


def test_coverage_ids_match_the_input_files():
    for name, key in (("dev", "dev"), ("test", "test")):
        assert {r["id"] for r in store.load_inputs(name)} == set(COVERAGE[key])


def test_dev_and_test_notes_do_not_overlap():
    dev = {r["input"].lower() for r in store.load_inputs("dev")}
    assert not dev & {r["input"].lower() for r in store.load_inputs("test")}


def test_input_order_does_not_follow_category():
    # Shuffled: the first ten test ids must not all share one category.
    first = [COVERAGE["test"][r["id"]] for r in store.load_inputs("test")[:10]]
    assert len(set(first)) > 3


def test_generator_reproduces_the_committed_files(tmp_path):
    from eval.design import build_inputs as b

    rows, coverage = b.build(b.TEST, "t", b.SEED + 1)
    assert rows == store.load_inputs("test") and coverage == COVERAGE["test"]


# ---- deadline parsing and label validation ---------------------------------


def test_parse_deadline():
    assert store.parse_deadline("") is None and store.parse_deadline("   ") is None
    assert store.parse_deadline("2026-11-03 17:00") == "2026-11-03T17:00:00+05:30"
    assert store.parse_deadline("2026-11-02 11:00") == "2026-11-02T11:00:00+05:30"


@pytest.mark.parametrize("bad", ["tomorrow", "03-11-2026 17:00", "2026-11-03", "2026-11-02 09:00", "2026-11-01 17:00"])
def test_parse_deadline_rejects_bad_or_past_values(bad):
    with pytest.raises(ValueError):
        store.parse_deadline(bad)


def good(**over):
    base = {"n_tasks": 1, "task_type": "normal", "has_dependency": False, "dependency_direction": "none",
            "dependency_team": "none", "deadline": None, "needs_clarification": False}
    return {**base, **over}


def test_valid_labels_pass():
    assert store.validate_labels(good(), TEAMS) == []
    dep = good(has_dependency=True, dependency_direction="need_from_them", dependency_team="payments")
    assert store.validate_labels(dep, TEAMS) == []
    assert store.validate_labels({"n_tasks": 3}, TEAMS) == []


@pytest.mark.parametrize(
    "labels",
    [
        good(task_type="call"),
        good(has_dependency=True),  # direction and team missing
        good(has_dependency=True, dependency_direction="need_from_them", dependency_team="marketing"),
        good(dependency_team="payments"),  # team without a dependency
        good(has_dependency=True, dependency_direction="need_from_them", dependency_team="unknown", needs_clarification=False),
        good(deadline="2026-11-03T17:00:00"),  # no offset
        good(deadline="2026-10-01T17:00:00+05:30"),  # before the reference time
        {"n_tasks": 3, "task_type": "email"},  # multi-task notes record only the count
        {"n_tasks": 0},
    ],
)
def test_inconsistent_labels_are_rejected(labels):
    assert store.validate_labels(labels, TEAMS)


# ---- sessions, with scripted answers ---------------------------------------


@pytest.fixture
def labeling(tmp_path, monkeypatch):
    items = [
        {"id": "x001", "input": "ask the payments team for the refund api documentation"},
        {"id": "x002", "input": "update the readme"},
        {"id": "x003", "input": "do the thing\nand the other thing"},
    ]
    path = tmp_path / "x.jsonl"
    path.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")
    monkeypatch.setattr(store, "SETS", {"x": "x.jsonl"})
    monkeypatch.setattr(store, "EVAL_DIR", tmp_path)
    monkeypatch.setattr(store, "LABEL_DIR", tmp_path / "labels")
    return tmp_path


def session(answers, set_name="x", **kw):
    queue = list(answers)
    shown: list[str] = []

    def inp(prompt):
        shown.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)

    out: list[str] = []
    label_tool.run(set_name, inp=inp, out=out.append, **kw)
    return "\n".join(out + shown)


DEP_PAYMENTS = ["1", "n", "y", "1", "2", "", "n", "", "wants docs"]  # need_from_them, payments (menu item 2)
PLAIN = ["1", "n", "n", "2026-11-03 17:00", "n", "", ""]


def test_full_session_saves_timestamped_human_labels(labeling):
    session(DEP_PAYMENTS + PLAIN + ["2", "", ""])  # third note: 2 tasks, count only
    history = store.read_history("x")
    assert [r["id"] for r in history] == ["x001", "x002", "x003"]
    first = history[0]
    assert first["status"] == "labeled" and first["labeled_by"] == store.LABELED_BY
    assert first["labeled_at"].endswith("+00:00") and first["revision"] == 1
    assert (first["has_dependency"], first["dependency_direction"], first["dependency_team"]) == (True, "need_from_them", "payments")
    assert first["notes"] == "wants docs"
    assert history[1]["deadline"] == "2026-11-03T17:00:00+05:30"
    assert history[2] == {**history[2], "n_tasks": 2} and "task_type" not in history[2]
    assert store.progress("x").remaining == 0


def test_session_resumes_and_skips_what_is_done(labeling):
    session(DEP_PAYMENTS)  # label one, then input runs out
    text = session(PLAIN)
    assert "update the readme" in text and "refund api documentation" not in text
    assert store.progress("x").labeled == 2


def test_quit_keeps_progress(labeling):
    session(DEP_PAYMENTS + ["q"])
    assert [r["id"] for r in store.read_history("x")] == ["x001"]


def test_skip_leaves_the_note_unlabeled(labeling):
    session(["s"] + PLAIN)
    assert [r["id"] for r in store.read_history("x")] == ["x002"]
    assert store.progress("x").remaining == 2


def test_flagging_records_the_reason_and_no_label(labeling):
    session(["f", "no rule covers 'sort of soon'"])
    record = store.read_history("x")[0]
    assert record["status"] == "flagged" and "sort of soon" in record["notes"] and "task_type" not in record
    assert store.progress("x").flagged == 1


def test_inconsistent_answers_make_the_person_redo_the_note(labeling):
    # unknown team but "no clarification needed" breaks rule C3: nothing is saved until fixed
    bad = ["1", "n", "y", "1", "5", "", "n"]
    fixed = ["1", "n", "y", "1", "5", "", "y", "", ""]
    text = session(bad + fixed)
    assert "do not fit together" in text
    record = store.read_history("x")[0]
    assert record["dependency_team"] == "unknown" and record["needs_clarification"] is True


def test_redo_before_saving_and_bad_deadline_are_reprompted(labeling):
    text = session(["1", "n", "n", "tomorrow", "2026-11-03 17:00", "n", "r"] + PLAIN)
    assert "use YYYY-MM-DD HH:MM" in text
    assert len(store.read_history("x")) == 1


def test_relabel_appends_a_new_revision_and_latest_wins(labeling):
    session(PLAIN)
    session(["1", "e", "n", "", "n", "", "corrected"], relabel="x001")
    history = [r for r in store.read_history("x") if r["id"] == "x001"]
    assert [r["revision"] for r in history] == [1, 2]
    latest = store.latest_by_id(store.read_history("x"))["x001"]
    assert latest["task_type"] == "email" and latest["notes"] == "corrected"


def test_help_key_prints_the_rules(labeling):
    assert "SHORT RULES" in session(["?", "q"])


# ---- blindness --------------------------------------------------------------


def test_tool_never_imports_the_app_or_any_model_code():
    for module in (label_tool, store):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = {
            (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
            for n in ast.walk(tree)
            if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in (n.names if isinstance(n, ast.Import) else [None])
        }
        assert not any(name.startswith(("backend", "openai", "langgraph", "httpx")) for name in imported), imported


def test_a_real_session_never_shows_categories_or_design_files():
    categories = set(COVERAGE["test"].values())
    text = session(["q"], set_name="test")
    shown_words = set(text.replace("_", " _ ").split())
    assert not any(c in text for c in categories if "_" in c), "category names must never be shown"
    assert "coverage" not in text and "design" not in text.lower().replace("designed", "")
    assert "Note 1 of 60" in text and shown_words
