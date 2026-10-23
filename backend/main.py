from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api import auth, meta
from backend.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="TaskPilot", lifespan=lifespan)
app.include_router(meta.router)
app.include_router(auth.router)
