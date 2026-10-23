import json

from pydantic import BaseModel

from backend.llm import AuditEntry, GroqLLM, Prompt, RawCompletion


class Out(BaseModel):
    n: int


PROMPT = Prompt(name="demo", version="v1", system="You turn things into JSON.")


def llm_with(replies):
    calls, audit = [], []
    queue = list(replies)

    def transport(model, messages, max_tokens):
        calls.append([dict(m) for m in messages])
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return RawCompletion(content=item, tokens_in=10, tokens_out=5, reasoning_tokens=2, latency_ms=7)

    return GroqLLM(transport, audit.append), calls, audit


def run(llm):
    return llm.generate(model="m", prompt=PROMPT, user="go", output=Out)


def test_valid_json_on_first_try():
    llm, calls, audit = llm_with([json.dumps({"n": 1})])
    result = run(llm)
    assert result.ok and result.first_try_ok and result.parsed.n == 1
    assert len(calls) == 1
    assert [(a.attempt, a.parsed_ok) for a in audit] == [(1, True)]


def test_malformed_json_is_retried_once_with_the_error_message():
    llm, calls, audit = llm_with(["{not json", json.dumps({"n": 2})])
    result = run(llm)
    assert result.ok and not result.first_try_ok and result.parsed.n == 2
    retry = calls[1]
    assert retry[-2] == {"role": "assistant", "content": "{not json"}
    assert "rejected" in retry[-1]["content"] and "not valid JSON" in retry[-1]["content"]
    assert [(a.attempt, a.parsed_ok) for a in audit] == [(1, False), (2, True)]


def test_schema_violation_is_retried_with_the_field_path():
    llm, calls, _ = llm_with([json.dumps({"n": "abc"}), json.dumps({"n": 3})])
    result = run(llm)
    assert result.ok and result.parsed.n == 3
    assert "n:" in calls[1][-1]["content"]


def test_two_failures_fall_back_without_raising():
    llm, calls, audit = llm_with(["nope", "still nope"])
    result = run(llm)
    assert not result.ok and result.parsed is None and result.error
    assert len(calls) == 2  # exactly one retry, never a loop
    assert [a.parsed_ok for a in audit] == [False, False]


def test_markdown_fences_are_tolerated():
    llm, _, _ = llm_with(['```json\n{"n": 4}\n```'])
    assert run(llm).parsed.n == 4


def test_transport_error_does_not_crash_and_is_audited():
    llm, calls, audit = llm_with([ConnectionError("boom")])
    result = run(llm)
    assert not result.ok and "ConnectionError" in result.error
    assert len(calls) == 1
    assert audit[0].parsed_ok is False and audit[0].tokens_in is None


def test_audit_records_tokens_and_prompt_version_but_no_message_text():
    llm, _, audit = llm_with([json.dumps({"n": 1})])
    run(llm)
    entry: AuditEntry = audit[0]
    assert (entry.model, entry.prompt_name, entry.prompt_version) == ("m", "demo", "v1")
    assert (entry.tokens_in, entry.tokens_out, entry.reasoning_tokens, entry.latency_ms) == (10, 5, 2, 7)
    assert "go" not in str(entry.__dict__.values())


def test_a_failing_audit_sink_does_not_break_the_call():
    def bad_sink(entry):
        raise RuntimeError("db down")

    llm = GroqLLM(lambda m, msgs, t: RawCompletion(content='{"n": 5}'), bad_sink)
    assert run(llm).parsed.n == 5
