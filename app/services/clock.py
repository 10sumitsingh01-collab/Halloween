"""
app/services/clock.py

Provides an injectable clock so that all "now" references in business logic
can be frozen in tests via FastAPI dependency_overrides.

Usage in service functions:
    def my_fn(db, clock: Callable[[], datetime] = utc_now): ...

Usage in routers:
    clock: Callable[[], datetime] = Depends(get_clock)

Override in tests:
    from app.services.clock import get_clock
    app.dependency_overrides[get_clock] = lambda: (lambda: fixed_time)
"""

from datetime import datetime, timezone
from typing import Callable


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """
    Normalize any datetime to timezone-aware UTC.

    SQLite returns naive datetimes from DateTime(timezone=True) columns
    (it stores them as UTC strings but strips tzinfo on read-back).
    This helper makes all datetime values safe for arithmetic comparisons
    against timezone-aware values from clock().
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Convention: all datetimes in this project are stored as UTC
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def get_clock() -> Callable[[], datetime]:
    """
    FastAPI dependency that returns the current clock callable.

    Routers depend on this; tests override it via dependency_overrides to
    freeze time without patching global state.
    """
    return utc_now
