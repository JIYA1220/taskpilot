"""In-process scheduler. One background thread calls run_tick on a fixed cadence.

Limitation, stated in the README: this is a single-process scheduler. Run one backend
process (or set SCHEDULER_ENABLED=false on the extras), otherwise reminders would repeat."""

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.engine import Engine
from sqlmodel import Session

from backend.clock import Clock, system_clock
from backend.config import Settings
from backend.reminders import run_tick

log = logging.getLogger(__name__)
JOB_ID = "tick"


def tick_once(engine: Engine, settings: Settings, clock: Clock = system_clock) -> None:
    try:
        with Session(engine) as session:
            result = run_tick(session, clock(), ZoneInfo(settings.timezone))
        if result.missed or result.reminders:
            log.info("tick: %d newly missed, %d reminders", len(result.missed), len(result.reminders))
    except Exception:  # a failed pass must not stop the scheduler; the next tick retries
        log.exception("scheduler tick failed")


def build_scheduler(engine: Engine, settings: Settings, clock: Clock = system_clock) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        tick_once,
        "interval",
        seconds=settings.scheduler_tick_seconds,
        args=[engine, settings, clock],
        id=JOB_ID,
        max_instances=1,  # never overlap two passes
        coalesce=True,  # after a stall, run once, not once per missed tick
    )
    return scheduler
