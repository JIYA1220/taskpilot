"""Load the demo organization: python -m scripts.seed (safe to re-run)."""

import os
import sys

from sqlmodel import Session

from backend.config import get_settings
from backend.db import get_engine, init_db
from backend.directory import seed_directory

# Synthetic accounts only. Override with DEMO_PASSWORD in the environment.
DEFAULT_DEMO_PASSWORD = "taskpilot-demo"


def main() -> int:
    settings = get_settings()
    init_db()
    with Session(get_engine()) as session:
        teams, users = seed_directory(
            session, settings, os.environ.get("DEMO_PASSWORD", DEFAULT_DEMO_PASSWORD)
        )
    print(f"Seeded {teams} teams and {users} users from {settings.directory_path.name}.")
    print("Log in as any directory user, e.g. jiya.sharma@acme.example.com (password: see README).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
