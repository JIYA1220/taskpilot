import sqlite3
from functools import lru_cache
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from backend.audit import db_audit_sink
from backend.clock import system_clock
from backend.config import get_settings
from backend.db import get_engine
from backend.directory import load_directory
from backend.llm import GroqLLM, groq_transport
from backend.organize.graph import GraphDeps, build_graph


def make_checkpointer(path: str) -> SqliteSaver:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


@lru_cache
def default_graph():
    """The production graph: real Groq transport, DB audit log, on-disk checkpoints."""
    settings = get_settings()
    llm = GroqLLM(groq_transport(settings), db_audit_sink(get_engine()))
    deps = GraphDeps(llm, settings, load_directory(settings.directory_path), system_clock)
    return build_graph(deps, make_checkpointer(settings.checkpoint_db))
