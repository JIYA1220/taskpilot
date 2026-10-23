from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from backend.api.deps import get_current_user
from backend.config import Settings, get_settings
from backend.db import get_session
from backend.models import Team, User

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    # Reports whether a key is configured, never the key itself.
    return {
        "status": "ok",
        "email_backend": settings.email_backend,
        "groq_key_configured": bool(settings.groq_api_key.get_secret_value()),
    }


@router.get("/directory")
def directory(session: Session = Depends(get_session), _: User = Depends(get_current_user)) -> list[dict]:
    teams = session.exec(select(Team).order_by(Team.name)).all()
    users = session.exec(select(User).order_by(User.name)).all()
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "owns": t.owns,
            "members": [{"id": u.id, "name": u.name, "email": u.email} for u in users if u.team_id == t.id],
        }
        for t in teams
    ]
