"""The freeze: what must be committed, what counts as a change, and what happens to old results."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from backend.config import Settings
from eval import freeze

PATHS = ["backend/prompts", "eval/metrics.py"]


def git(root, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t.t", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "backend" / "prompts").mkdir(parents=True)
    (root / "eval").mkdir()
    (root / "backend" / "prompts" / "extract.v1.md").write_text("prompt one")
    (root / "eval" / "metrics.py").write_text("# scoring")
    (root / "README.md").write_text("docs")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")

    inputs, labels = tmp_path / "dataset.jsonl", tmp_path / "labels.jsonl"
    inputs.write_text('{"id": "t001", "input": "a note"}\n')
    labels.write_text('{"id": "t001", "status": "labeled"}\n')
    monkeypatch.setattr(freeze.store, "input_path", lambda s: inputs)
    monkeypatch.setattr(freeze.store, "label_path", lambda s: labels)
    monkeypatch.setattr(freeze, "get_settings", lambda: Settings(_env_file=None, app_env="test"))
    return SimpleNamespace(root=root, results=tmp_path / "results", inputs=inputs, labels=labels)


def create(repo, **kw):
    return freeze.create(repo.root, repo.results, PATHS, require_labels=False, **kw)


def verify(repo):
    return freeze.verify(repo.root, repo.results, PATHS)


def commit(repo, message="change"):
    git(repo.root, "add", "-A")
    git(repo.root, "commit", "-q", "-m", message)


# ---- creating -----------------------------------------------------------------


def test_the_freeze_records_the_committed_state(repo):
    record = create(repo)
    assert record["git_commit"] == subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo.root, capture_output=True, text=True).stdout.strip()
    assert set(record["git_ids"]) == set(PATHS) and record["prompt_sha256"].keys() == {"extract.v1.md"}
    assert record["models"]["main"] == "openai/gpt-oss-120b" and record["fixed_now"].startswith("2026-11-02T09:00")
    assert record["n_test_items"] == 1 and len(record["freeze_id"]) == 10
    assert json.loads((repo.results / "freeze.json").read_text()) == record


def test_it_refuses_to_freeze_with_uncommitted_changes(repo):
    (repo.root / "backend" / "prompts" / "extract.v1.md").write_text("prompt one, edited")
    with pytest.raises(RuntimeError, match="uncommitted changes in frozen files"):
        create(repo)
    assert not (repo.results / "freeze.json").exists()


def test_it_refuses_to_freeze_until_every_test_label_exists(repo, monkeypatch):
    monkeypatch.setattr(freeze.store, "progress", lambda s: SimpleNamespace(remaining=12, flagged=2))
    with pytest.raises(RuntimeError, match="12 test note.*no label.*2 test note.*flagged"):
        freeze.create(repo.root, repo.results, PATHS)


def test_labels_ready_is_empty_when_everything_is_labeled(monkeypatch):
    monkeypatch.setattr(freeze.store, "progress", lambda s: SimpleNamespace(remaining=0, flagged=0))
    assert freeze.test_labels_ready() == []


# ---- verifying ----------------------------------------------------------------


def test_a_fresh_freeze_holds_and_unrelated_changes_do_not_break_it(repo):
    create(repo)
    assert verify(repo) == []
    (repo.root / "README.md").write_text("docs, edited")
    commit(repo)
    assert verify(repo) == []


def test_no_freeze_means_no_final_run(repo):
    assert verify(repo) == ["there is no freeze yet: run python -m eval.freeze first"]


def test_a_committed_prompt_change_breaks_the_freeze(repo):
    create(repo)
    (repo.root / "backend" / "prompts" / "extract.v1.md").write_text("prompt two")
    commit(repo)
    assert "backend/prompts changed since the freeze" in verify(repo)


def test_an_uncommitted_change_to_a_frozen_file_breaks_it_too(repo):
    create(repo)
    (repo.root / "eval" / "metrics.py").write_text("# scoring, adjusted")
    problems = verify(repo)
    assert any("uncommitted changes in frozen files" in p and "eval/metrics.py" in p for p in problems)


def test_changed_scoring_code_breaks_the_freeze(repo):
    create(repo)
    (repo.root / "eval" / "metrics.py").write_text("# scoring, adjusted")
    commit(repo)
    assert "eval/metrics.py changed since the freeze" in verify(repo)


def test_changed_test_notes_or_labels_break_the_freeze(repo):
    create(repo)
    repo.inputs.write_text('{"id": "t001", "input": "a different note"}\n')
    assert "the test notes changed since the freeze" in verify(repo)
    repo.inputs.write_text('{"id": "t001", "input": "a note"}\n')
    assert verify(repo) == []
    repo.labels.write_text('{"id": "t001", "status": "labeled", "task_type": "email"}\n')
    assert "the test labels changed since the freeze" in verify(repo)


# ---- superseding old results ------------------------------------------------


def test_a_new_freeze_keeps_the_old_results_but_marks_them_superseded(repo):
    first = create(repo)
    (repo.results / "predictions").mkdir()
    (repo.results / "predictions" / "m.jsonl").write_text("{}\n")
    for name in ("metrics.json", "summary.md", "ERROR_ANALYSIS.md"):
        (repo.results / name).write_text(name)

    (repo.root / "backend" / "prompts" / "extract.v1.md").write_text("prompt two")
    commit(repo)
    second = create(repo)

    assert second["freeze_id"] != first["freeze_id"]
    old = repo.results / "superseded" / first["freeze_id"]
    assert (old / "metrics.json").exists() and (old / "predictions" / "m.jsonl").exists() and (old / "freeze.json").exists()
    assert "superseded" in (old / "SUPERSEDED.md").read_text()
    assert not (repo.results / "metrics.json").exists() and not (repo.results / "predictions").exists()
    assert verify(repo) == []


def test_a_first_freeze_has_nothing_to_supersede(repo):
    create(repo)
    assert not (repo.results / "superseded").exists()


def test_the_cli_check_reports_status(repo, capsys, monkeypatch):
    monkeypatch.setattr(freeze, "ROOT", repo.root)
    monkeypatch.setattr(freeze, "RESULTS", repo.results)
    monkeypatch.setattr(freeze, "verify", lambda: ["something changed"])
    assert freeze.main(["--check"]) == 1 and "something changed" in capsys.readouterr().out
    monkeypatch.setattr(freeze, "verify", lambda: [])
    assert freeze.main(["--check"]) == 0
    assert freeze.main(["--nonsense"]) == 2
