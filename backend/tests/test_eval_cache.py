import httpx
import pytest
from openai import APIConnectionError

from backend.llm import RawCompletion
from eval.cache import CachingTransport, EvalInfraError, Ledger, RateLimiter, cache_key, slug

MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]


class Real:
    def __init__(self, reply="{}", tokens=(100, 50), error=None):
        self.calls, self.reply, self.tokens, self.error = 0, reply, tokens, error

    def __call__(self, model, messages, max_tokens):
        self.calls += 1
        if self.error:
            raise self.error
        return RawCompletion(self.reply, self.tokens[0], self.tokens[1], 7, 1234)


def make(tmp_path, real, **kw):
    ledger = Ledger()
    return CachingTransport(real, tmp_path, "low", ledger, **kw), ledger


# ---- the key ------------------------------------------------------------------


def test_the_key_changes_with_anything_that_could_change_the_answer():
    base = cache_key("m", MESSAGES, 2000, "low")
    assert base == cache_key("m", [dict(x) for x in MESSAGES], 2000, "low")
    assert base != cache_key("m2", MESSAGES, 2000, "low")
    assert base != cache_key("m", MESSAGES, 1000, "low")
    assert base != cache_key("m", MESSAGES, 2000, "high")
    edited_prompt = [{"role": "system", "content": "sys, edited"}, MESSAGES[1]]
    assert base != cache_key("m", edited_prompt, 2000, "low"), "a changed prompt must never reuse an old reply"


def test_slug_makes_a_safe_file_name():
    assert slug("openai/gpt-oss-120b") == "openai-gpt-oss-120b"


# ---- hits, misses and persistence -------------------------------------------


def test_a_repeat_request_is_served_from_the_cache_with_the_original_numbers(tmp_path):
    real = Real("answer")
    transport, ledger = make(tmp_path, real)
    first = transport("m", MESSAGES, 2000)
    second = transport("m", MESSAGES, 2000)
    assert real.calls == 1 and first.content == second.content == "answer"
    assert (second.tokens_in, second.tokens_out, second.reasoning_tokens, second.latency_ms) == (100, 50, 7, 1234)
    assert (ledger.api_calls, ledger.cache_hits, ledger.tokens_spent, ledger.hit_rate) == (1, 1, 150, 0.5)


def test_the_cache_survives_a_restart(tmp_path):
    make(tmp_path, Real("kept"))[0]("m", MESSAGES, 2000)
    real = Real("should not be called")
    transport, ledger = make(tmp_path, real)
    assert transport("m", MESSAGES, 2000).content == "kept"
    assert real.calls == 0 and ledger.api_calls == 0 and ledger.cache_hits == 1


def test_a_different_message_or_model_is_a_miss(tmp_path):
    real = Real()
    transport, _ = make(tmp_path, real)
    transport("m", MESSAGES, 2000)
    transport("m", MESSAGES[:1] + [{"role": "user", "content": "other"}], 2000)
    transport("other-model", MESSAGES, 2000)
    assert real.calls == 3
    assert {p.name for p in tmp_path.iterdir()} == {"m.jsonl", "other-model.jsonl"}


def test_each_new_reply_is_written_immediately(tmp_path):
    transport, _ = make(tmp_path, Real())
    transport("m", MESSAGES, 2000)
    assert len((tmp_path / "m.jsonl").read_text().splitlines()) == 1


# ---- failures are not model mistakes -----------------------------------------


@pytest.mark.parametrize("error", [APIConnectionError(request=httpx.Request("POST", "http://x")), OSError("down")])
def test_network_and_api_errors_stop_the_run_and_are_never_cached(tmp_path, error):
    real = Real(error=error)
    transport, ledger = make(tmp_path, real)
    with pytest.raises(EvalInfraError, match="run again later"):
        transport("m", MESSAGES, 2000)
    assert ledger.api_calls == 0 and not list(tmp_path.glob("*.jsonl"))
    real.error = None
    assert transport("m", MESSAGES, 2000).content == "{}", "the same request works once the problem is gone"


def test_the_session_token_budget_stops_before_the_next_call_but_cache_hits_are_free(tmp_path):
    real = Real(tokens=(600, 500))
    transport, _ = make(tmp_path, real, token_budget=1000)
    transport("m", MESSAGES, 2000)  # spends 1100, over the budget
    transport("m", MESSAGES, 2000)  # a cache hit costs nothing
    with pytest.raises(EvalInfraError, match="token budget of 1000 reached"):
        transport("m", [{"role": "user", "content": "new"}], 2000)
    assert real.calls == 1


# ---- the rate limiter ---------------------------------------------------------


class FakeTime:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_the_limiter_lets_calls_through_until_the_minute_budget_is_used():
    t = FakeTime()
    limiter = RateLimiter(3000, estimate_per_call=1000, clock=t.clock, sleep=t.sleep)
    for _ in range(3):
        assert limiter.wait() == 0
        limiter.record(1000)
    assert t.slept == []
    waited = limiter.wait()  # a fourth call has to wait for the first to leave the 60 s window
    assert 59 < waited < 61 and len(t.slept) == 1


def test_the_window_slides_so_old_usage_stops_counting():
    t = FakeTime()
    limiter = RateLimiter(2000, estimate_per_call=1000, clock=t.clock, sleep=t.sleep)
    limiter.wait(), limiter.record(1000), limiter.wait(), limiter.record(1000)
    t.now += 61
    assert limiter.wait() == 0


def test_a_single_call_bigger_than_the_whole_budget_is_not_blocked_forever():
    t = FakeTime()
    assert RateLimiter(500, estimate_per_call=1000, clock=t.clock, sleep=t.sleep).wait() == 0


def test_the_limiter_is_used_and_records_real_usage(tmp_path):
    t = FakeTime()
    limiter = RateLimiter(1000, estimate_per_call=500, clock=t.clock, sleep=t.sleep)
    transport, _ = make(tmp_path, Real(tokens=(450, 400)), limiter=limiter)
    transport("m", MESSAGES, 2000)
    transport("m", [{"role": "user", "content": "b"}], 2000)  # 850 used + 500 estimate > 1000: must wait
    assert len(t.slept) == 1


def test_ledger_with_no_requests_has_no_hit_rate():
    assert Ledger().hit_rate is None and Ledger().as_dict()["api_calls_made"] == 0
