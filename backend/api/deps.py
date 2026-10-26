from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from backend.clock import Clock, system_clock
from backend.config import Settings, get_settings
from backend.db import get_session
from backend.mail.sender import EmailSender, default_sender
from backend.models import User
from backend.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_clock() -> Clock:
    return system_clock


def get_llm():
    from backend.organize.runtime import default_llm

    return default_llm()


def get_sender() -> EmailSender:
    return default_sender()


def get_graph():
    # Imported lazily so importing the API never builds the graph or reads .env.
    from backend.organize.runtime import default_graph

    return default_graph()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if credentials is None:
        raise unauthorized
    user_id = decode_access_token(credentials.credentials, settings)
    user = session.get(User, user_id) if user_id is not None else None
    if user is None:
        raise unauthorized
    return user
