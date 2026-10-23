from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from backend import models  # noqa: F401  (registers tables on SQLModel.metadata)
from backend.config import get_settings


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, **kwargs)


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


def init_db(engine: Engine | None = None) -> None:
    SQLModel.metadata.create_all(engine or get_engine())


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
