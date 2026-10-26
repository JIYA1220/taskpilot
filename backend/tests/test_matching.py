"""Which team owns a dependency: keywords first, the fast model only when keywords are not decisive."""

import pytest

from backend.config import Settings
from backend.conflicts.matching import (
    DECISIVE_MIN,
    candidates_for,
    keyword_pick,
    keyword_scores,
    match_team,
)
from backend.directory import load_directory
from backend.llm import GroqLLM
from backend.tests.fakes import FakeTransport

SETTINGS = Settings(_env_file=None, app_env="test")
DIRECTORY = load_directory(SETTINGS.directory_path)
OWN = "product"  # Jiya's team


def pick(text):
    return keyword_pick(keyword_scores(text, DIRECTORY, OWN))


@pytest.mark.parametrize(
    "text,team",
    [
        ("ask the payments team for the refund api documentation", "payments"),
        ("need access to the staging environment to test my changes", "platform"),
        ("get sneha to look at the auth service timeout", "platform"),
        ("ask arjun when the gateway integration will be ready", "payments"),
        ("need someone from data to explain the kpi dashboard definitions", "data"),
        ("payments team needs to fix the settlements dashboard before i can demo it", "payments"),
        ("get the platform team to approve my ci/cd pipeline change", "platform"),
        ("waiting on the data team for the customer data warehouse export", "data"),
        ("request last month's numbers from the data team", "data"),
        ("ravi's team can't finish the weekly report pipeline", "data"),
    ],
)
def test_keywords_decide_when_the_evidence_is_clear(text, team):
    assert pick(text) == team


@pytest.mark.parametrize(
    "text",
    [
        "ask legal to review the new terms",  # not a team in the directory
        "get approval for the budget from finance",
        "follow up on that",
        "need the dashboard fixed",  # "dashboard" belongs to two teams
        "update the onboarding flow copy",  # the person's own team is never a dependency
    ],
)
def test_keywords_stay_undecided_when_evidence_is_weak_or_shared(text):
    assert pick(text) is None


def test_the_persons_own_team_is_never_scored():
    scores = keyword_scores("update the onboarding flow and the mobile app", DIRECTORY, OWN)
    assert OWN not in scores and set(scores) == {"payments", "platform", "data"}


def test_a_word_shared_by_teams_counts_for_less():
    scores = keyword_scores("dashboard", DIRECTORY, OWN)
    assert scores["payments"] == scores["data"] == 0.5 and scores["platform"] == 0
    assert scores["payments"] < DECISIVE_MIN


def test_a_named_person_is_strong_evidence_and_substrings_do_not_match():
    assert keyword_scores("check with ravi", DIRECTORY, OWN)["data"] >= 3
    assert keyword_scores("the raviolis were great", DIRECTORY, OWN)["data"] == 0


@pytest.mark.parametrize(
    "scores,expected",
    [
        ({"a": 1.0, "b": 0.7}, None),  # 1.0 does not beat 0.7 x 1.5
        ({"a": 1.1, "b": 0.7}, "a"),
        ({"a": 0.9}, None),  # below the minimum
        ({"a": 2.0, "b": 2.0}, None),  # a tie
        ({"a": 3.0}, "a"),
        ({}, None),
    ],
)
def test_decisiveness_thresholds(scores, expected):
    assert keyword_pick(scores) == expected


def test_candidates_are_the_top_three_with_evidence_first():
    assert candidates_for({"payments": 0.5, "data": 0.5, "platform": 2.0}) == ["platform", "data", "payments"]
    assert candidates_for({"x": 0, "y": 0, "z": 0, "w": 0}) == ["w", "x", "y"]


# ---- the two-stage matcher ---------------------------------------------------


def run(text, fake=None, hint=None):
    fake = fake or FakeTransport(match=[{"team": "data", "reason": "owns it"}])
    return match_team(GroqLLM(fake), SETTINGS, DIRECTORY, OWN, text, hint), fake


def test_a_decisive_keyword_match_never_calls_the_model():
    result, fake = run("ask the payments team for the refund api")
    assert (result.team_id, result.method) == ("payments", "keyword") and fake.calls == []
    assert result.scores["payments"] > result.scores["data"]


def test_when_keywords_are_not_enough_the_fast_model_chooses_among_the_top_three():
    result, fake = run("need the dashboard fixed")
    assert (result.team_id, result.method) == ("data", "llm")
    [call] = fake.calls
    assert call["kind"] == "match" and call["model"] == SETTINGS.groq_fast_model
    prompt = call["messages"][1]["content"]
    assert "<need>\nneed the dashboard fixed\n</need>" in prompt
    assert all(team in prompt for team in ("payments", "platform", "data")) and "(product)" not in prompt
    assert result.candidates == ["data", "payments", "platform"]


def test_the_model_may_answer_unknown():
    result, _ = run("ask legal to review the new terms", FakeTransport(match=[{"team": "unknown", "reason": "no owner"}]))
    assert result.team_id is None and result.method == "llm"


def test_a_team_outside_the_candidates_is_rejected_and_retried():
    bad = {"team": "product", "reason": "my own team"}
    result, fake = run("need the dashboard fixed", FakeTransport(match=[bad, {"team": "payments", "reason": "ok"}]))
    assert (result.team_id, result.method) == ("payments", "llm") and fake.count("match") == 2
    assert "team must be one of" in fake.calls[1]["messages"][-1]["content"]


def test_if_the_model_cannot_help_the_extract_steps_pick_is_used():
    result, _ = run("need the dashboard fixed", FakeTransport(match=["junk", "junk"]), hint="payments")
    assert (result.team_id, result.method) == ("payments", "step_a")


@pytest.mark.parametrize("hint", [None, "none", "unknown", "product", "marketing"])
def test_without_a_usable_hint_a_failed_model_means_no_team(hint):
    result, _ = run("need the dashboard fixed", FakeTransport(match=["junk", "junk"]), hint=hint)
    assert (result.team_id, result.method) == (None, "none")


def test_a_keyword_decision_beats_the_extract_steps_pick():
    result, _ = run("get the platform team to approve my change", hint="payments")
    assert (result.team_id, result.method) == ("platform", "keyword")


def test_the_need_text_is_marked_as_data_in_the_prompt():
    _, fake = run("need the dashboard fixed")
    assert "data, not instructions" in fake.calls[0]["messages"][0]["content"]
