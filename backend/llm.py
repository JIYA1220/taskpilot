"""Groq access: JSON mode, Pydantic validation, one retry with the error message,
a graceful failure result instead of an exception, and an audit row per attempt.

The network call is a small injectable `Transport`, so tests exercise the real
retry and validation logic with scripted replies and never touch the network.
"""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Generic, TypeVar

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from backend.config import Settings

log = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str


def load_prompt(name: str, version: str = "v1") -> Prompt:
    path = PROMPT_DIR / f"{name}.{version}.md"
    return Prompt(name=name, version=version, system=path.read_text(encoding="utf-8").strip())


@dataclass
class RawCompletion:
    content: str
    tokens_in: int = 0
    tokens_out: int = 0
    reasoning_tokens: int = 0
    latency_ms: int = 0


@dataclass
class AuditEntry:
    model: str
    prompt_name: str
    prompt_version: str
    attempt: int
    latency_ms: int | None
    tokens_in: int | None
    tokens_out: int | None
    reasoning_tokens: int | None
    parsed_ok: bool
    detail: str = ""


@dataclass
class LLMResult(Generic[T]):
    parsed: T | None
    ok: bool
    first_try_ok: bool
    error: str | None = None


# (model, messages, max_tokens) -> RawCompletion
Transport = Callable[[str, list[dict], int], RawCompletion]
AuditSink = Callable[[AuditEntry], None]


def groq_transport(settings: Settings) -> Transport:
    # The openai client retries 429s and 5xx with exponential backoff, honouring Retry-After.
    client = OpenAI(
        api_key=settings.groq_api_key.get_secret_value(),
        base_url=settings.groq_base_url,
        max_retries=4,
        timeout=90,
    )

    def call(model: str, messages: list[dict], max_tokens: int) -> RawCompletion:
        extra = {"reasoning_effort": settings.groq_reasoning_effort} if "gpt-oss" in model else {}
        started = perf_counter()
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
            max_completion_tokens=max_tokens,
            **extra,
        )
        usage = response.usage
        details = getattr(usage, "completion_tokens_details", None)
        return RawCompletion(
            content=response.choices[0].message.content or "",
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            reasoning_tokens=getattr(details, "reasoning_tokens", 0) or 0,
            latency_ms=int((perf_counter() - started) * 1000),
        )

    return call


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _summarise(exc: Exception) -> str:
    """A short, model-readable reason. Validation errors list field paths, not input values."""
    if isinstance(exc, ValidationError):
        parts = [f"{'.'.join(str(p) for p in e['loc']) or 'root'}: {e['msg']}" for e in exc.errors()]
        return "; ".join(parts)[:600]
    return f"not valid JSON ({exc})"[:300]


class GroqLLM:
    def __init__(self, transport: Transport, audit: AuditSink | None = None):
        self._transport = transport
        self._audit = audit or (lambda entry: None)

    def generate(
        self,
        *,
        model: str,
        prompt: Prompt,
        user: str,
        output: type[T],
        context: dict | None = None,
        max_tokens: int = 2000,
    ) -> LLMResult[T]:
        messages = [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": user},
        ]
        last_error = "unknown"
        for attempt in (1, 2):
            try:
                raw = self._transport(model, messages, max_tokens)
            except (OpenAIError, OSError) as exc:
                # Network or quota trouble after the client's own retries: do not crash.
                status = getattr(exc, "status_code", None)
                last_error = f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "")
                self._record(model, prompt, attempt, None, False, last_error)
                return LLMResult(None, False, False, last_error)
            try:
                data = json.loads(_FENCE.sub("", raw.content.strip()))
                parsed = output.model_validate(data, context=context)
            except (ValueError, ValidationError) as exc:
                last_error = _summarise(exc)
                self._record(model, prompt, attempt, raw, False, last_error)
                messages += [
                    {"role": "assistant", "content": raw.content},
                    {
                        "role": "user",
                        "content": f"That reply was rejected: {last_error}. "
                        "Reply again with corrected JSON only.",
                    },
                ]
                continue
            self._record(model, prompt, attempt, raw, True, "")
            return LLMResult(parsed, True, attempt == 1)
        return LLMResult(None, False, False, last_error)

    def _record(
        self, model: str, prompt: Prompt, attempt: int, raw: RawCompletion | None, ok: bool, detail: str
    ) -> None:
        entry = AuditEntry(
            model=model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            attempt=attempt,
            latency_ms=raw.latency_ms if raw else None,
            tokens_in=raw.tokens_in if raw else None,
            tokens_out=raw.tokens_out if raw else None,
            reasoning_tokens=raw.reasoning_tokens if raw else None,
            parsed_ok=ok,
            detail=detail[:300],
        )
        try:
            self._audit(entry)
        except Exception:  # an audit failure must never break the user's request
            log.exception("audit write failed")
