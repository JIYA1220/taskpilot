import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.config import Settings, get_settings
from backend.db import get_session
from backend.directory import seed_directory
from backend.main import app

DEMO_PASSWORD = "test-password-123"


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: tests never read a developer's real .env.
    return Settings(_env_file=None, app_env="test", jwt_secret="test-secret-test-secret-test-secret-1234")


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture
def seeded(session, settings):
    seed_directory(session, settings, DEMO_PASSWORD)
    return session


@pytest.fixture
def client(engine, settings):
    def _session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()
