import pytest
from pydantic import ValidationError

from backend.config import Settings


def test_health_reports_status_without_leaking_the_key(client, settings):
    settings.groq_api_key = type(settings.groq_api_key)("gsk-not-a-real-key-for-this-test")
    body = client.get("/api/health").json()
    assert body == {"status": "ok", "email_backend": "mailpit", "groq_key_configured": True}
    assert "not-a-real-key" not in str(body)


def test_defaults_are_the_safe_ones():
    s = Settings(_env_file=None)
    assert s.email_backend == "mailpit"
    assert s.allowlist == []  # empty allowlist: gmail would block everything
    assert s.groq_main_model == "openai/gpt-oss-120b"
    assert s.groq_fast_model == "openai/gpt-oss-20b"
    assert s.groq_reasoning_effort == "low"


def test_allowlist_parsing():
    s = Settings(_env_file=None, email_allowlist=" A@x.com, b@y.com ,, ")
    assert s.allowlist == ["a@x.com", "b@y.com"]


def test_missing_jwt_secret_is_random_in_dev_but_rejected_in_production():
    a, b = Settings(_env_file=None), Settings(_env_file=None)
    assert a.jwt_secret.get_secret_value() != b.jwt_secret.get_secret_value() != ""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production")


def test_secret_is_masked_in_repr():
    s = Settings(_env_file=None, jwt_secret="super-secret-value-super-secret-value-1")
    assert "super-secret" not in repr(s)
