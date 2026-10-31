"""The lean evaluation's data: the notes, the three-field labels, and the blind labeling tool."""

import ast
import json
from collections import Counter
from pathlib import Path

import pytest

from eval import label_tool, labels as store

EVAL = Path(store.EVAL_DIR)
TEAMS = ["product", "payments", "platform", "data"]
COVERAGE = json.loads((EVAL / "design" / "coverage.json").read_text(encoding="utf-8"))
ARCHIVE = EVAL / "archive" / "full_v1"


# ---- the note sets, against the lean spec -------------------------------------


def test_set_sizes_and_shape():
    dev, test = store.load_inputs("dev"), store.load_inputs("test")
    assert (len(dev), len(test)) == (10, 30)
    for rows in (dev, test):
        assert all(set(r) == {"id", "input"} for r in rows), "input files hold only id and text, no labels or categories"
        assert all(r["input"].strip() for r in rows)
        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids))


def test_every_note_is_a_single_task():
    for name in ("dev", "test"):
        for row in store.load_inputs(name):
            assert "\n" not in row["input"] and not row["input"].lstrip().startswith(("-", "1.", "*")), row


def test_the_test_set_meets_the_requested_composition():
    cats = list(COVERAGE["test"].values())
    dependency = [c for c in cats if c.startswith("dep_")]
    reverse = [c for c in cats if c.startswith("dep_reverse")]
    email = [c for c in cats if c.startswith("email")]
    with_deadline = [c for c in cats if c.endswith("+deadline")]
    assert len(cats) == 30
    assert len(dependency) >= 10 and len(reverse) >= 3, "at least 10 dependency notes, at least 3 of them reverse"
    assert len(email) == 8, "about 8 email tasks"
    assert len(with_deadline) >= 8, "at least 8 notes with a relative deadline"
    assert Counter(c.split("+")[0] for c in cats) == {
        "dep_need": 7, "dep_reverse": 3, "email": 8, "plain": 7, "trap": 5,
    }


def test_the_dev_set_mirrors_the_mix_in_miniature():
    cats = [c.split("+")[0] for c in COVERAGE["dev"].values()]
    assert Counter(cats) == {"dep_need": 2, "dep_reverse": 1, "email": 2, "plain": 4, "trap": 1}
    assert any(c.endswith("+deadline") for c in COVERAGE["dev"].values())


def test_coverage_ids_match_the_input_files():
    for name in ("dev", "test"):
        assert {r["id"] for r in store.load_inputs(name)} == set(COVERAGE[name])


def test_dev_and_test_notes_do_not_overlap_each_other_or_any_archived_set():
    dev = {r["input"].lower() for r in store.load_inputs("dev")}
    test = {r["input"].lower() for r in store.load_inputs("test")}
    old = {
        json.loads(line)["input"].lower()
        for path in (ARCHIVE / "dev.jsonl", ARCHIVE / "dataset.jsonl", EVAL / "archive" / "round1_human" / "dataset.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert not dev & test, "dev and test must not share a note"
    assert not test & old, "the round 2 test notes must be fresh: no model has seen them"


# ---- round 2: the AI-written gold labels ---------------------------------------


def test_every_test_note_has_exactly_one_ai_gold_row():
    from eval.design import ai_gold

    notes = [r["input"] for r in store.load_inputs("test")]
    rows = [r[1] for r in ai_gold.TEST_GOLD]
    assert sorted(notes) == sorted(rows) and len(set(rows)) == 30


def test_the_ai_gold_labels_are_valid_and_meet_the_requested_composition():
    from eval.design import ai_gold

    labels = [ai_gold.labels_for(row) for row in ai_gold.TEST_GOLD]
    for row, label in zip(ai_gold.TEST_GOLD, labels):
        assert store.validate_labels(label, TEAMS) == [], row[1]
    deps = [x["dependency"] for x in labels if x["dependency"]]
    assert len(deps) >= 10 and sum(d["direction"] == "they_need_from_me" for d in deps) >= 3
    assert sum(x["task_type"] == "email" for x in labels) == 8
    assert sum(1 for x in labels if x["deadline"]) >= 8


def test_the_ai_gold_agrees_with_its_own_design_categories():
    from eval.design import ai_gold

    for category, note, task_type, dependency, deadline, rule in ai_gold.TEST_GOLD:
        base = category.split("+")[0]
        assert (deadline is not None) == category.endswith("+deadline"), note
        assert (task_type == "email") == (base == "email"), note
        assert (dependency is not None) == (base in ("dep_need", "dep_reverse")), note
        if base == "dep_reverse":
            assert dependency[1] == "they_need_from_me", note
        assert rule.strip(), "every label states the rule it applies"


def test_the_deadline_helpers_reproduce_the_guides_table():
    from eval.design import ai_gold as g

    assert g.day_only("fri") == "2026-11-06T23:59:00+05:30"  # a weekday alone
    assert g.day_only("mon") == "2026-11-02T23:59:00+05:30"  # 'today' alone
    assert g.day_only("tue") == "2026-11-03T23:59:00+05:30"  # 'tomorrow' alone
    assert g.morning("tue") == "2026-11-03T12:00:00+05:30"
    assert g.tonight() == "2026-11-02T21:00:00+05:30"
    assert g.bare_hour(5) == "2026-11-02T17:00:00+05:30" and g.bare_hour(6) == "2026-11-02T18:00:00+05:30"
    assert g.bare_hour(9) == "2026-11-02T09:00:00+05:30" and g.bare_hour(11) == "2026-11-02T11:00:00+05:30"
    assert g.in_hours(2) == "2026-11-02T11:00:00+05:30" and g.in_hours(3) == "2026-11-02T12:00:00+05:30"
    assert g.clock(16) == "2026-11-02T16:00:00+05:30"


def test_writing_the_ai_gold_marks_every_record_as_not_human_written(tmp_path, monkeypatch):
    from eval.design import ai_gold

    monkeypatch.setattr(store, "LABEL_DIR", tmp_path / "labels")
    assert ai_gold.write_labels("test") == 30
    records = store.read_history("test")
    assert len(records) == 30 and {r["labeled_by"] for r in records} == {store.AI_LABELED_BY}
    assert "NOT written by a human" in store.AI_LABELED_BY and all(r["notes"].startswith("AI-applied rule:") for r in records)
    assert store.progress("test").labeled == 30


def test_the_committed_test_labels_are_all_marked_as_ai_written():
    records = store.read_history("test")
    assert len(records) == 30 and {r["labeled_by"] for r in records} == {store.AI_LABELED_BY}


def test_the_human_round_is_archived_with_its_labels_and_the_reason():
    round1 = EVAL / "archive" / "round1_human"
    assert (round1 / "labels" / "dataset.labels.jsonl").exists() and (round1 / "dataset.jsonl").exists()
    text = (round1 / "README.md").read_text(encoding="utf-8").lower() if (round1 / "README.md").exists() else ""
    assert "invalidated" in text and "human" in text


def test_input_order_does_not_follow_category():
    first = [COVERAGE["test"][r["id"]].split("+")[0] for r in store.load_inputs("test")[:10]]
    assert len(set(first)) > 2


def test_generator_reproduces_the_committed_files():
    from eval.design import build_inputs as b

    for rows_cov, name, prefix, seed, source in (
        (b.build(b.TEST, "t", b.SEED + 1), "test", "t", b.SEED + 1, b.TEST),
        (b.build(b.DEV, "d", b.SEED), "dev", "d", b.SEED, b.DEV),
    ):
        rows, coverage = rows_cov
        assert rows == store.load_inputs(name) and coverage == COVERAGE[name]


def test_the_worked_examples_are_not_in_the_dev_or_test_sets():
    text = (EVAL / "WORKED_EXAMPLES.md").read_text(encoding="utf-8").lower()
    rows = [line.split("|")[1].strip() for line in text.splitlines() if line.startswith("| ") and not line.startswith(("| note", "| ---", "|---"))]
    notes = {r["input"].lower() for name in ("dev", "test") for r in store.load_inputs(name)}
    assert len(rows) >= 10 and not set(rows) & notes, "teaching examples must never be dataset notes"


def test_the_old_labels_are_archived_and_no_longer_where_the_tool_looks():
    assert (ARCHIVE / "labels" / "dataset.labels.jsonl").exists() and (ARCHIVE / "README.md").exists()
    assert "superseded" in (ARCHIVE / "README.md").read_text(encoding="utf-8").lower()


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
    return {"task_type": "normal", "dependency": None, "deadline": None, **over}


def dep(team="payments", direction="need_from_them"):
    return {"team": team, "direction": direction}


@pytest.mark.parametrize(
    "labels",
    [good(), good(task_type="email"), good(dependency=dep()), good(dependency=dep("data", "they_need_from_me")),
     good(deadline="2026-11-03T17:00:00+05:30")],
)
def test_valid_labels_pass(labels):
    assert store.validate_labels(labels, TEAMS) == []


@pytest.mark.parametrize(
    "labels",
    [
        good(task_type="call"),
        good(dependency=dep("product")),  # your own team is never a dependency
        good(dependency=dep("unknown")),  # an unidentified team is flagged, not labeled
        good(dependency=dep("marketing")),
        good(dependency=dep(direction="sideways")),
        good(dependency="payments"),
        good(dependency={"team": "payments"}),
        good(deadline="2026-11-03T17:00:00"),  # no offset
        good(deadline="2026-10-01T17:00:00+05:30"),  # before the reference time
        good(deadline="soon"),
        {"task_type": "normal", "dependency": None},  # a field is missing
        {**good(), "needs_clarification": True},  # dropped fields are rejected, not ignored
        {**good(), "n_tasks": 1},
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
        {"id": "x003", "input": "email aarav the notes"},
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


# type, dependency y, direction 1 (need), team 1 (payments), deadline none, confirm, note
DEP_PAYMENTS = ["n", "y", "1", "1", "", "", "wants docs"]
# type, dependency n, a deadline, confirm, note
PLAIN = ["n", "n", "2026-11-03 17:00", "", ""]
EMAIL = ["e", "n", "", "", ""]


def test_a_session_saves_timestamped_human_labels_with_exactly_three_fields(labeling):
    session(DEP_PAYMENTS + PLAIN + EMAIL)
    history = store.read_history("x")
    assert [r["id"] for r in history] == ["x001", "x002", "x003"]
    first = history[0]
    assert first["status"] == "labeled" and first["labeled_by"] == store.LABELED_BY and first["revision"] == 1
    assert first["labeled_at"].endswith("+00:00") and first["notes"] == "wants docs"
    assert first["task_type"] == "normal" and first["dependency"] == {"team": "payments", "direction": "need_from_them"}
    assert first["deadline"] is None
    assert history[1]["deadline"] == "2026-11-03T17:00:00+05:30" and history[1]["dependency"] is None
    assert history[2]["task_type"] == "email"
    for record in history:
        assert {k for k in record if k in ("n_tasks", "needs_clarification", "has_dependency")} == set(), "dropped fields are never stored"
    assert store.progress("x").remaining == 0


def test_the_team_menu_never_offers_your_own_team(labeling):
    text = session(DEP_PAYMENTS)
    assert "[1] payments  [2] platform  [3] data" in text and "product" not in text.split("Team:")[1].split("\n")[0]


def test_both_directions_and_every_other_team_can_be_chosen(labeling):
    session(["n", "y", "2", "3", "", "", "", "n", "y", "1", "2", "", "", ""])  # they need from me -> data; need from them -> platform
    a, b = store.read_history("x")
    assert a["dependency"] == {"team": "data", "direction": "they_need_from_me"}
    assert b["dependency"] == {"team": "platform", "direction": "need_from_them"}


def test_session_resumes_and_skips_what_is_done(labeling):
    session(DEP_PAYMENTS)  # label one, then the input runs out
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


def test_redo_before_saving_and_a_bad_deadline_are_reprompted(labeling):
    text = session(["n", "n", "tomorrow", "2026-11-03 17:00", "r"] + PLAIN)
    assert "use YYYY-MM-DD HH:MM" in text
    assert len(store.read_history("x")) == 1


def test_relabel_appends_a_new_revision_and_latest_wins(labeling):
    session(PLAIN)
    session(["e", "n", "", "", "corrected"], relabel="x001")
    history = [r for r in store.read_history("x") if r["id"] == "x001"]
    assert [r["revision"] for r in history] == [1, 2]
    latest = store.latest_by_id(store.read_history("x"))["x001"]
    assert latest["task_type"] == "email" and latest["deadline"] is None and latest["notes"] == "corrected"


def test_relabeling_an_unknown_id_says_so(labeling):
    assert "No note with id nope" in session([], relabel="nope")


def test_the_help_key_prints_the_rules(labeling):
    text = session(["?", "q"])
    assert "SHORT RULES" in text and "Every note is ONE task" in text and "needs clarification" not in text.lower()


# ---- reviewing your own labels ---------------------------------------------


def test_review_shows_each_note_with_its_current_label_and_marks_the_rest(labeling):
    session(PLAIN + ["f", "unclear"])  # x001 labeled, x002 flagged, x003 untouched
    lines = []
    label_tool.show_review("x", lines.append)
    text = "\n".join(lines)
    assert "x001  | ask the payments team for the refund api documentation" in text
    assert "type: normal | dependency: no | deadline: 2026-11-03T17:00:00+05:30" in text
    assert "FLAGGED (unclear)" in text and "NOT LABELED" in text and "--relabel <id>" in text


def test_review_follows_a_relabel(labeling):
    session(PLAIN)
    session(DEP_PAYMENTS, relabel="x001")
    lines = []
    label_tool.show_review("x", lines.append)
    assert "dependency: yes (need_from_them, payments) | deadline: none" in "\n".join(lines)


def test_summary_counts_the_answers_so_a_pattern_is_visible(labeling):
    session(PLAIN + EMAIL)
    lines = []
    label_tool.show_summary("x", lines.append)
    text = "\n".join(lines)
    assert "2 labeled notes" in text and "task type: email x1, normal x1" in text
    assert "notes with a dependency: 0, without: 2" in text and "deadline: 1 with a deadline, 1 with none" in text


def test_summary_with_a_dependency(labeling):
    session(DEP_PAYMENTS + PLAIN)
    lines = []
    label_tool.show_summary("x", lines.append)
    text = "\n".join(lines)
    assert "dependency direction: need_from_them x1" in text and "dependency team: payments x1" in text
    assert "notes with a dependency: 1, without: 1" in text


def test_review_and_summary_run_on_the_command_line(labeling, capsys):
    assert label_tool.main(["x", "--review"]) == 0 and "NOT LABELED" in capsys.readouterr().out
    assert label_tool.main(["x", "--summary"]) == 0 and "0 labeled notes" in capsys.readouterr().out


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


def test_a_real_session_never_shows_categories_or_design_files(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "LABEL_DIR", tmp_path / "labels")  # an empty label folder, whatever is labeled for real
    categories = {c.split("+")[0] for c in COVERAGE["test"].values()}
    text = session(["q"], set_name="test")
    assert not any(c in text for c in categories if "_" in c), "category names must never be shown"
    assert "coverage" not in text and "Note 1 of 30" in text
