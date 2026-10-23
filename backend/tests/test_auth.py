from datetime import UTC, datetime, timedelta

from backend.config import Settings
from backend.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from backend.tests.conftest import DEMO_PASSWORD

EMAIL = "jiya.sharma@acme.example.com"


def login(client, email=EMAIL, password=DEMO_PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_password_hash_is_not_plaintext_and_verifies():
    hashed = hash_password("hunter2-hunter2")
    assert "hunter2" not in hashed
    assert verify_password("hunter2-hunter2", hashed)
    assert not verify_password("wrong", hashed)


def test_verify_password_rejects_unknown_user_hash():
    assert not verify_password("anything", None)
    assert not verify_password("anything", "not-a-hash")


def test_token_round_trip_and_tamper(settings):
    token = create_access_token(7, settings)
    assert decode_access_token(token, settings) == 7
    assert decode_access_token(token + "x", settings) is None


def test_expired_token_is_rejected(settings):
    old = datetime.now(UTC) - timedelta(minutes=settings.jwt_expire_minutes + 5)
    assert decode_access_token(create_access_token(7, settings, now=old), settings) is None


def test_token_signed_with_other_secret_is_rejected(settings):
    other = Settings(_env_file=None, app_env="test", jwt_secret="another-secret-another-secret-another-1")
    assert decode_access_token(create_access_token(7, other), settings) is None


def test_login_success_and_me(client, seeded):
    response = login(client)
    assert response.status_code == 200
    token = response.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL
    assert "password" not in me.text


def test_login_is_case_insensitive_on_email(client, seeded):
    assert login(client, email=EMAIL.upper().replace("ACME.EXAMPLE.COM", "acme.example.com")).status_code == 200


def test_login_wrong_password_and_unknown_user_look_the_same(client, seeded):
    wrong = login(client, password="nope")
    unknown = login(client, email="nobody@acme.example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_me_requires_a_valid_token(client, seeded):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_token_for_deleted_user_is_rejected(client, settings):
    token = create_access_token(999, settings)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
