"""Which team owns a dependency?

Two stages, as designed: cheap keyword matching against what each team owns first; and only
when that is not decisive, the fast model chooses among the top three candidates. The result
records which method decided, so the methods can be compared in the evaluation."""

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from backend.config import Settings
from backend.directory import Directory
from backend.llm import GroqLLM, load_prompt

STOPWORDS = {"a", "an", "the", "and", "of", "to", "for", "with", "in", "on", "from", "team", "my", "our", "need", "needs"}
PHRASE_BONUS = 2.0  # a whole owned system ("refund API") appears in the text
NAME_BONUS = 2.0  # a team name word ("payments") appears
PERSON_BONUS = 3.0  # a member is named ("Sneha")
DECISIVE_MIN = 1.0  # the best team must score at least this ...
DECISIVE_RATIO = 1.5  # ... and beat the runner-up by this factor
MAX_CANDIDATES = 3


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOPWORDS]


@dataclass
class MatchResult:
    team_id: str | None  # None: no team identified
    method: str  # keyword, llm, step_a or none
    candidates: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


def keyword_scores(text: str, directory: Directory, own_team: str) -> dict[str, float]:
    """Score every team except the person's own against `text`. A word that several teams'
    keywords share (like "dashboard") counts for less, so it cannot decide by itself."""
    keyword_sets = {t.id: {w for system in t.owns for w in _words(system)} | set(_words(t.name)) for t in directory.teams}
    teams_with = {}
    for team_id, words in keyword_sets.items():
        for w in words:
            teams_with.setdefault(w, set()).add(team_id)
    text_words = set(_words(text))
    flat = " ".join(re.findall(r"[a-z]+", text.lower()))
    scores = {}
    for team in directory.teams:
        if team.id == own_team:
            continue
        score = 0.0
        for system in team.owns:
            if " ".join(re.findall(r"[a-z]+", system.lower())) in flat:
                score += PHRASE_BONUS
        for w in keyword_sets[team.id] & text_words:
            score += (NAME_BONUS if w in _words(team.name) and len(teams_with[w]) == 1 else 1.0) / len(teams_with[w])
        for user in (u for u in directory.users if u.team_id == team.id):
            parts = _words(user.name)
            if parts[0] in text_words or " ".join(parts) in flat:
                score += PERSON_BONUS
        scores[team.id] = round(score, 3)
    return scores


def keyword_pick(scores: dict[str, float]) -> str | None:
    """The team keywords decide on, or None when the evidence is weak or tied."""
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if not ranked or ranked[0][1] < DECISIVE_MIN:
        return None
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    return ranked[0][0] if ranked[0][1] > runner_up * DECISIVE_RATIO else None


class MatchOutput(BaseModel):
    team: str
    reason: str = Field(default="", max_length=300)

    @field_validator("team")
    @classmethod
    def _one_of_the_candidates(cls, value: str, info: ValidationInfo) -> str:
        allowed = set((info.context or {}).get("candidates", [])) | {"unknown"}
        if value not in allowed:
            raise ValueError(f"team must be one of {sorted(allowed)}")
        return value


def candidates_for(scores: dict[str, float]) -> list[str]:
    """The top three teams: those with any evidence first, then the rest, so the model always has a short list."""
    ranked = sorted(scores, key=lambda t: (-scores[t], t))
    return ranked[:MAX_CANDIDATES]


def match_team(
    llm: GroqLLM,
    settings: Settings,
    directory: Directory,
    own_team: str,
    text: str,
    hint_team: str | None = None,
) -> MatchResult:
    """`hint_team` is the extract step's own pick; it is the fallback if the model cannot help."""
    scores = keyword_scores(text, directory, own_team)
    candidates = candidates_for(scores)
    decided = keyword_pick(scores)
    if decided:
        return MatchResult(decided, "keyword", candidates, scores)

    by_id = {t.id: t for t in directory.teams}
    listing = "\n".join(f"- {c} ({by_id[c].name}): owns {', '.join(by_id[c].owns)}" for c in candidates)
    members = "\n".join(f"- {u.name} ({u.team_id})" for u in directory.users if u.team_id in candidates)
    result = llm.generate(
        model=settings.groq_fast_model,
        prompt=load_prompt("match_team"),
        user=f"Candidate teams:\n{listing}\n\nPeople:\n{members}\n\n<need>\n{text}\n</need>",
        output=MatchOutput,
        context={"candidates": candidates},
        max_tokens=600,
    )
    if result.ok and result.parsed:
        team = None if result.parsed.team == "unknown" else result.parsed.team
        return MatchResult(team, "llm", candidates, scores)
    if hint_team in by_id and hint_team != own_team:  # the model could not help: keep the extract step's pick
        return MatchResult(hint_team, "step_a", candidates, scores)
    return MatchResult(None, "none", candidates, scores)
