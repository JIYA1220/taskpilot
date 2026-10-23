"""The demo organization. eval/directory.json is the single source of truth:
the seed script, Step A's team enum and the gold labels all read from it."""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, EmailStr
from sqlmodel import Session, select

from backend.config import Settings
from backend.models import Team, User
from backend.security import hash_password


class TeamEntry(BaseModel):
    id: str
    name: str
    description: str
    owns: list[str]


class UserEntry(BaseModel):
    name: str
    email: EmailStr
    team_id: str


class Directory(BaseModel):
    org_name: str
    domain: str
    teams: list[TeamEntry]
    users: list[UserEntry]

    def team_ids(self) -> list[str]:
        return [t.id for t in self.teams]


def load_directory(path: Path) -> Directory:
    return Directory.model_validate_json(path.read_text(encoding="utf-8"))


def directory_digest(path: Path) -> str:
    """Hash of the canonical JSON, so formatting-only edits do not break the freeze."""
    canonical = json.dumps(json.loads(path.read_text(encoding="utf-8")), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def seed_directory(session: Session, settings: Settings, demo_password: str) -> tuple[int, int]:
    """Insert or update teams and users. Safe to run repeatedly. Returns (teams, users)."""
    directory = load_directory(settings.directory_path)
    for entry in directory.teams:
        team = session.get(Team, entry.id) or Team(id=entry.id, name=entry.name)
        team.name, team.description, team.owns = entry.name, entry.description, list(entry.owns)
        session.add(team)
    session.flush()
    for entry in directory.users:
        email = entry.email.lower()
        user = session.exec(select(User).where(User.email == email)).first()
        if user is None:
            user = User(
                name=entry.name,
                email=email,
                team_id=entry.team_id,
                password_hash=hash_password(demo_password),
            )
        else:
            user.name, user.team_id = entry.name, entry.team_id
        session.add(user)
    session.commit()
    return len(directory.teams), len(directory.users)
