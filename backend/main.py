from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api import auth, meta, organize, tasks
from backend.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="TaskPilot", lifespan=lifespan)
app.include_router(meta.router)
app.include_router(auth.router)
app.include_router(organize.router)
app.include_router(tasks.router)
