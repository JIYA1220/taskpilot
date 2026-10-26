from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api import auth, meta, notifications, organize, tasks
from backend.config import get_settings
from backend.db import get_engine, init_db
from backend.scheduler import build_scheduler, tick_once


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    settings = get_settings()
    scheduler = None
    if settings.scheduler_enabled:
        scheduler = build_scheduler(get_engine(), settings)
        scheduler.start()
        tick_once(get_engine(), settings)  # catch up straight away after a restart
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)


app = FastAPI(title="TaskPilot", lifespan=lifespan)
app.include_router(meta.router)
app.include_router(auth.router)
app.include_router(organize.router)
app.include_router(tasks.router)
app.include_router(notifications.router)
