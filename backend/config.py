import logging
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///data/taskpilot.db"

    groq_api_key: SecretStr = SecretStr("")
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_main_model: str = "openai/gpt-oss-120b"
    groq_fast_model: str = "openai/gpt-oss-20b"
    # gpt-oss are reasoning models: low keeps latency and token use down.
    groq_reasoning_effort: Literal["low", "medium", "high"] = "low"
    checkpoint_db: str = "data/checkpoints.db"

    jwt_secret: SecretStr = SecretStr("")
    jwt_expire_minutes: int = 480

    email_backend: Literal["mailpit", "gmail"] = "mailpit"
    # Comma-separated string (not a list) so an empty value parses cleanly.
    email_allowlist: str = ""
    mailpit_host: str = "localhost"
    mailpit_port: int = 1025
    mail_from: str = "taskpilot@acme.example.com"
    # A failed send is retried with exponential backoff: base, 2x base, 4x base ...
    email_max_attempts: int = 5
    email_retry_base_seconds: int = 60

    # In-process scheduler: how often it looks for missed deadlines and due reminders.
    # Turn it off (SCHEDULER_ENABLED=false) if you ever run more than one process.
    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 30

    org_domain: str = "acme.example.com"
    timezone: str = "Asia/Kolkata"
    directory_path: Path = REPO_ROOT / "eval" / "directory.json"

    @property
    def allowlist(self) -> list[str]:
        return [a.strip().lower() for a in self.email_allowlist.split(",") if a.strip()]

    @model_validator(mode="after")
    def _ensure_jwt_secret(self) -> "Settings":
        if self.jwt_secret.get_secret_value():
            return self
        if self.app_env == "production":
            raise ValueError("JWT_SECRET must be set when APP_ENV=production")
        log.warning("JWT_SECRET is empty: using a random one, logins reset on restart")
        self.jwt_secret = SecretStr(secrets.token_urlsafe(48))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
