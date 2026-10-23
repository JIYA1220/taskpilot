from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlmodel import Session, select

from backend.api.deps import get_current_user
from backend.config import Settings, get_settings
from backend.db import get_session
from backend.models import User
from backend.security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    team_id: str


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    user = session.exec(select(User).where(User.email == body.email.lower())).first()
    # verify_password runs a dummy hash for unknown emails to keep timing similar.
    if not verify_password(body.password, user.password_hash if user else None) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    return TokenResponse(access_token=create_access_token(user.id, settings))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
