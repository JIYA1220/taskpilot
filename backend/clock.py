from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def system_clock() -> datetime:
    """Current time as an aware UTC datetime. Tests inject a fixed clock instead."""
    return datetime.now(UTC)
