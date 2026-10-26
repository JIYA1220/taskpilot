"""The evaluation's network layer: a response cache, a token-rate limiter, and one rule about failures.

Cache key = sha256 of everything that could change the answer: the model, the full message list
(which contains the prompt text and the fixed "now"), max tokens, temperature and reasoning
effort. So editing a prompt, the directory or the date can never reuse an old reply. Entries are
appended to a file the moment they arrive, so a crash or a quota stop loses nothing and a rerun
resumes by hitting the cache.

Failure rule: a network, quota or API error is NOT a model mistake. It raises EvalInfraError,
which the runner catches to stop cleanly and tell the person to resume later. It must never be
scored as wrong JSON."""

import hashlib
import json
import re
import time
from collections import deque
from pathlib import Path

from openai import OpenAIError

from backend.llm import RawCompletion, Transport


class EvalInfraError(Exception):
    """The run cannot continue (rate limit, quota, network, budget). Resume later; the cache keeps all progress."""


def slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")


def cache_key(model: str, messages: list[dict], max_tokens: int, reasoning_effort: str) -> str:
    payload = json.dumps(
        {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0, "reasoning_effort": reasoning_effort},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class Ledger:
    """What a run actually did, for the report: API calls made, cache hits, tokens spent."""

    def __init__(self):
        self.api_calls = 0
        self.cache_hits = 0
        self.tokens_spent = 0

    @property
    def requests(self) -> int:
        return self.api_calls + self.cache_hits

    @property
    def hit_rate(self) -> float | None:
        return self.cache_hits / self.requests if self.requests else None

    def as_dict(self) -> dict:
        return {"api_calls_made": self.api_calls, "cache_hits": self.cache_hits, "cache_hit_rate": self.hit_rate, "tokens_spent_by_new_calls": self.tokens_spent}


class RateLimiter:
    """Keeps new calls inside a tokens-per-minute budget using a sliding 60 s window."""

    def __init__(self, tokens_per_minute: int, estimate_per_call: int = 1800, clock=time.monotonic, sleep=time.sleep):
        self.budget, self.estimate = tokens_per_minute, estimate_per_call
        self._clock, self._sleep = clock, sleep
        self._used: deque[tuple[float, int]] = deque()

    def wait(self) -> float:
        """Block until one more call fits in the window. Returns the seconds waited."""
        waited = 0.0
        while True:
            now = self._clock()
            while self._used and now - self._used[0][0] >= 60:
                self._used.popleft()
            if sum(t for _, t in self._used) + self.estimate <= self.budget or not self._used:
                return waited
            pause = 60 - (now - self._used[0][0]) + 0.05
            self._sleep(pause)
            waited += pause

    def record(self, tokens: int) -> None:
        self._used.append((self._clock(), tokens))


class CachingTransport:
    """Wraps a real transport with the response cache, the limiter, a session budget and the failure rule."""

    def __init__(
        self,
        real: Transport,
        cache_dir: Path,
        reasoning_effort: str,
        ledger: Ledger,
        limiter: RateLimiter | None = None,
        token_budget: int | None = None,
    ):
        self.real, self.dir, self.effort, self.ledger = real, cache_dir, reasoning_effort, ledger
        self.limiter, self.budget = limiter, token_budget
        self._memory: dict[str, dict] = {}
        self._loaded: set[str] = set()

    def _file(self, model: str) -> Path:
        return self.dir / f"{slug(model)}.jsonl"

    def _load(self, model: str) -> None:
        if model in self._loaded:
            return
        self._loaded.add(model)
        path = self._file(model)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._memory[row["key"]] = row

    def __call__(self, model: str, messages: list[dict], max_tokens: int) -> RawCompletion:
        self._load(model)
        key = cache_key(model, messages, max_tokens, self.effort)
        row = self._memory.get(key)
        if row is not None:
            self.ledger.cache_hits += 1
            return RawCompletion(row["content"], row["tokens_in"], row["tokens_out"], row["reasoning_tokens"], row["latency_ms"])

        if self.budget is not None and self.ledger.tokens_spent >= self.budget:
            raise EvalInfraError(f"session token budget of {self.budget} reached; run again to continue (progress is cached)")
        if self.limiter:
            self.limiter.wait()
        try:
            raw = self.real(model, messages, max_tokens)
        except (OpenAIError, OSError) as exc:
            status = getattr(exc, "status_code", None)
            raise EvalInfraError(f"{type(exc).__name__}{f' (HTTP {status})' if status else ''}: run again later; progress is cached") from exc
        used = raw.tokens_in + raw.tokens_out
        if self.limiter:
            self.limiter.record(used)
        self.ledger.api_calls += 1
        self.ledger.tokens_spent += used
        row = {"key": key, "model": model, "content": raw.content, "tokens_in": raw.tokens_in, "tokens_out": raw.tokens_out, "reasoning_tokens": raw.reasoning_tokens, "latency_ms": raw.latency_ms}
        self._memory[key] = row
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._file(model).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return raw


