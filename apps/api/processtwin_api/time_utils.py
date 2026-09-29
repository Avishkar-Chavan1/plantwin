"""Timestamp normalization for database values and externally supplied telemetry."""

from __future__ import annotations

from datetime import UTC, datetime


def as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC timestamp.

    PostgreSQL preserves ``DateTime(timezone=True)`` offsets, while SQLite returns
    those values without tzinfo. ProcessTwin persists and compares all timestamps
    in UTC, so a naive value read from SQLite is interpreted as the stored UTC
    instant rather than the host machine's local time.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
