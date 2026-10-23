"""Freeze the demo directory once it is approved: python -m scripts.freeze_directory

Gold labels use these team ids, so after labeling starts the directory must not
change. This writes eval/directory.lock; a test fails if directory.json differs.
To change the directory later, delete the lock deliberately and relabel.
"""

import sys

from backend.config import get_settings
from backend.directory import directory_digest

LOCK_NAME = "directory.lock"


def main() -> int:
    path = get_settings().directory_path
    lock = path.with_name(LOCK_NAME)
    lock.write_text(directory_digest(path) + "\n", encoding="utf-8")
    print(f"Froze {path.name} -> {lock.name}. Commit the lock file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
