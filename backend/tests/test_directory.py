from pathlib import Path

from sqlmodel import select

from backend.config import Settings
from backend.directory import directory_digest, load_directory, seed_directory
from backend.models import Team, User
from backend.tests.conftest import DEMO_PASSWORD

DIRECTORY = Settings(_env_file=None).directory_path


def test_directory_matches_the_spec():
    d = load_directory(DIRECTORY)
    assert len(d.teams) == 4
    assert len(d.users) == 8
    assert all(2 <= len(t.owns) <= 3 for t in d.teams)


def test_directory_ids_are_unique_and_users_reference_real_teams():
    d = load_directory(DIRECTORY)
    ids = d.team_ids()
    assert len(ids) == len(set(ids))
    emails = [u.email.lower() for u in d.users]
    assert len(emails) == len(set(emails))
    assert {u.team_id for u in d.users} <= set(ids)
    assert all(u.email.endswith("@" + d.domain) for u in d.users)


def test_every_team_has_a_member():
    d = load_directory(DIRECTORY)
    assert {u.team_id for u in d.users} == set(d.team_ids())


def test_directory_is_unchanged_since_freeze_if_a_lock_exists():
    lock = Path(DIRECTORY).with_name("directory.lock")
    if not lock.exists():
        return  # not frozen yet: labeling has not started
    assert lock.read_text().strip() == directory_digest(DIRECTORY), (
        "eval/directory.json changed after it was frozen; gold labels use its team ids"
    )


def test_seed_is_idempotent(session, settings):
    assert seed_directory(session, settings, DEMO_PASSWORD) == (4, 8)
    assert seed_directory(session, settings, DEMO_PASSWORD) == (4, 8)
    assert len(session.exec(select(Team)).all()) == 4
    assert len(session.exec(select(User)).all()) == 8


def test_seeded_passwords_are_hashed(seeded):
    assert all(DEMO_PASSWORD not in u.password_hash for u in seeded.exec(select(User)).all())


def test_directory_endpoint_requires_login_and_lists_members(client, seeded):
    assert client.get("/api/directory").status_code == 401
    token = client.post(
        "/api/auth/login",
        json={"email": "jiya.sharma@acme.example.com", "password": DEMO_PASSWORD},
    ).json()["access_token"]
    teams = client.get("/api/directory", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(teams) == 4
    assert sum(len(t["members"]) for t in teams) == 8
