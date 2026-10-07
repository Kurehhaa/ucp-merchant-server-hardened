"""Small shared helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Current time as a *naive* UTC datetime.

    Drop-in for the deprecated ``datetime.utcnow()``: the models store naive
    UTC timestamps, so the value stays comparable with existing data.
    """
    return datetime.now(UTC).replace(tzinfo=None)
