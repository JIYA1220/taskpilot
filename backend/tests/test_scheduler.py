from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import backend.main as main
from backend.config import Settings
from backend.models import Notification, Task
from backend.scheduler import JOB_ID, build_scheduler, tick_once

SETTINGS = Settings(_env_file=None, app_env="test", scheduler_tick_seconds=30)


def test_scheduler_has_one_non_overlapping_coalescing_tick_job(engine):
    scheduler = build_scheduler(engine, SETTINGS)
    job = scheduler.get_job(JOB_ID)  # not started, so it is still a pending job
    assert job is not None and job.id == JOB_ID and job.max_instances == 1 and job.coalesce is True
    assert job.trigger.interval.total_seconds() == 30


def test_tick_once_does_real_work_with_an_injected_clock(engine, seeded):
    seeded.add(Task(owner_id=1, title="Late", deadline=datetime(2026, 11, 2, 4, 0, tzinfo=UTC)))
    seeded.commit()
    tick_once(engine, SETTINGS, lambda: datetime(2026, 11, 2, 5, 0, tzinfo=UTC))  # 10:30 IST, a Monday
    with Session(engine) as s:
        assert s.exec(select(Task)).one().status == "missed"
        assert {n.kind for n in s.exec(select(Notification))} == {"missed"}


def test_a_failing_tick_never_raises(engine):
    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("db is down")

    tick_once(Broken(), SETTINGS, lambda: datetime(2026, 11, 2, 5, 0, tzinfo=UTC))  # must simply log


@pytest.mark.parametrize("enabled", [True, False])
def test_lifespan_starts_and_stops_the_scheduler_only_when_enabled(monkeypatch, engine, enabled):
    events = []

    class Spy:
        def start(self):
            events.append("start")

        def shutdown(self, wait=False):
            events.append("shutdown")

    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "get_engine", lambda: engine)
    monkeypatch.setattr(main, "get_settings", lambda: Settings(_env_file=None, app_env="test", scheduler_enabled=enabled))
    monkeypatch.setattr(main, "build_scheduler", lambda *a, **k: Spy())
    monkeypatch.setattr(main, "tick_once", lambda *a, **k: events.append("catch-up"))
    with TestClient(main.app):
        pass
    assert events == (["start", "catch-up", "shutdown"] if enabled else [])
