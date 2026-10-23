"""The LangGraph flow: pause for answers, resume, and survive a restart."""

import pytest

from backend.clock import Clock
from backend.config import Settings
from backend.directory import load_directory
from backend.llm import GroqLLM
from backend.organize.graph import GraphDeps, build_graph, is_paused, resume_organize, start_organize
from backend.organize.runtime import make_checkpointer
from backend.tests.fakes import FIXED_NOW, NO_QUESTIONS, REFUND, REPORT, FakeTransport, extract_reply, rewrite_reply

SETTINGS = Settings(_env_file=None, app_env="test")
DIRECTORY = load_directory(SETTINGS.directory_path)
CLOCK: Clock = lambda: FIXED_NOW  # noqa: E731


def make_graph(fake: FakeTransport, checkpoint_path: str = ":memory:"):
    deps = GraphDeps(GroqLLM(fake), SETTINGS, DIRECTORY, CLOCK)
    return build_graph(deps, make_checkpointer(checkpoint_path))


def two_task_fake():
    return FakeTransport(
        extract=[extract_reply(REFUND, REPORT)],
        rewrite=[rewrite_reply({"priority": "P1", "priority_reason": "Blocks others."}, {"priority": "P2"})],
    )


def test_pauses_with_at_most_three_questions_then_finishes_after_answers():
    fake = two_task_fake()
    graph = make_graph(fake)
    first = start_organize(graph, "1:a", "ask payments team\nsend report", "product")
    assert first.status == "needs_answers" and is_paused(graph, "1:a")
    assert 1 <= len(first.questions) <= 3
    assert first.tasks == [] and fake.count("rewrite") == 0  # nothing rewritten before answers
    assert first.extracted_titles == [REFUND["title"], REPORT["title"]]

    answers = {q.id: "P1" for q in first.questions}
    done = resume_organize(graph, "1:a", answers)
    assert done.status == "ready" and not is_paused(graph, "1:a")
    assert len(done.tasks) == 2 and done.tasks[0].priority == "P1"
    assert fake.count("extract") == 1, "resuming must not repeat the extract call"
    assert fake.count("rewrite") == 1


def test_all_questions_can_be_skipped():
    graph = make_graph(two_task_fake())
    first = start_organize(graph, "1:a", "x", "product")
    done = resume_organize(graph, "1:a", {})
    assert done.status == "ready" and len(done.tasks) == 2
    assert all(c.answer is None for t in done.tasks for c in t.clarifications)


def test_no_unknowns_skips_the_interrupt():
    fake = FakeTransport(extract=[extract_reply(NO_QUESTIONS)], rewrite=[rewrite_reply({})])
    graph = make_graph(fake)
    out = start_organize(graph, "1:a", "send weekly report to Ravi by 5", "product")
    assert out.status == "ready" and len(out.tasks) == 1 and out.questions == []
    assert not is_paused(graph, "1:a")


def test_bad_model_output_still_ends_ready_for_manual_review():
    fake = FakeTransport(extract=["junk", "junk"], rewrite=[rewrite_reply({})])
    out = start_organize(make_graph(fake), "1:a", "line one\nline two", "product")
    assert out.status == "ready" and len(out.tasks) == 2
    assert all(t.needs_manual_review for t in out.tasks)
    assert out.warnings
    assert fake.count("rewrite") == 0


def test_threads_are_independent():
    graph = make_graph(two_task_fake())
    a = start_organize(graph, "1:a", "x", "product")
    b = start_organize(graph, "2:b", "y", "product")
    resume_organize(graph, "1:a", {})
    assert is_paused(graph, "2:b") and not is_paused(graph, "1:a")
    assert a.thread_id != b.thread_id


def test_paused_run_survives_a_restart(tmp_path):
    db = str(tmp_path / "checkpoints.db")
    first_process = make_graph(two_task_fake(), db)
    paused = start_organize(first_process, "1:a", "ask payments team\nsend report", "product")
    assert paused.status == "needs_answers"

    # "Restart": a brand new graph, saver and connection on the same file. The extract
    # call is not scripted again, so it would fail loudly if the run were not restored.
    second_fake = FakeTransport(extract=["unused"], rewrite=[rewrite_reply({"priority": "P1"}, {})])
    second_process = make_graph(second_fake, db)
    assert is_paused(second_process, "1:a")
    done = resume_organize(second_process, "1:a", {q.id: "Today 17:00" for q in paused.questions})
    assert done.status == "ready" and len(done.tasks) == 2
    assert second_fake.count("extract") == 0
    assert done.tasks[0].title == REFUND["title"]


def test_resume_with_unknown_question_ids_ignores_them():
    graph = make_graph(two_task_fake())
    start_organize(graph, "1:a", "x", "product")
    done = resume_organize(graph, "1:a", {"q99": "injected"})
    assert done.status == "ready"
    assert all(c.answer is None for t in done.tasks for c in t.clarifications)
