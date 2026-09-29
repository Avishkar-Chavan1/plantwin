from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TelemetryMessage(BaseModel):
    """Validated read-only measurement after connector-specific protocol parsing."""

    model_config = ConfigDict(extra="forbid")
    source_key: str = Field(min_length=1, max_length=512)
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=32)
    timestamp: datetime
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    timestamp_source: Literal["SOURCE", "RECEIVED"] = "SOURCE"


@dataclass(frozen=True)
class SourceHealth:
    status: Literal["CONNECTED", "DISCONNECTED", "STALE", "ERROR"]
    last_message_at: datetime | None
    last_success_at: datetime | None
    message_rate_per_minute: float
    latency_ms: float | None
    error_count: int
    freshness_s: float | None
    last_error: str | None


class SourceHealthMonitor:
    """Thread-safe enough for one connector event loop; no raw payloads or credentials logged."""

    def __init__(self, stale_after_s: float = 30.0, rate_window_s: float = 60.0) -> None:
        if stale_after_s <= 0 or rate_window_s <= 0:
            raise ValueError("Freshness thresholds must be positive")
        self.stale_after_s = stale_after_s
        self.rate_window_s = rate_window_s
        self.connected = False
        self.last_message_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.latency_ms: float | None = None
        self.error_count = 0
        self.last_error: str | None = None
        self._message_times: deque[datetime] = deque()

    def set_connected(self, connected: bool) -> None:
        self.connected = connected

    def record_message(self, message: TelemetryMessage, *, successful: bool) -> None:
        now = message.received_at.astimezone(UTC)
        timestamp = message.timestamp.astimezone(UTC)
        self.last_message_at = now
        self._message_times.append(now)
        self._trim(now)
        latency = (now - timestamp).total_seconds() * 1000
        self.latency_ms = max(0.0, latency) if isfinite(latency) else None
        if successful:
            self.last_success_at = now
            self.last_error = None

    def record_error(self, error: BaseException | str) -> None:
        self.error_count += 1
        self.last_error = str(error)[:500]

    def _trim(self, now: datetime) -> None:
        while self._message_times and (now - self._message_times[0]).total_seconds() > self.rate_window_s:
            self._message_times.popleft()

    def snapshot(self, now: datetime | None = None) -> SourceHealth:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if self.last_message_at is None:
            freshness = None
            status = "ERROR" if self.error_count else ("CONNECTED" if self.connected else "DISCONNECTED")
        else:
            freshness = max(0.0, (current - self.last_message_at.astimezone(UTC)).total_seconds())
            status = (
                "ERROR"
                if self.last_error is not None
                else "STALE"
                if self.connected and freshness > self.stale_after_s
                else "CONNECTED"
                if self.connected
                else "DISCONNECTED"
            )
        self._trim(current)
        rate = len(self._message_times) * 60.0 / self.rate_window_s
        return SourceHealth(
            status=status,
            last_message_at=self.last_message_at,
            last_success_at=self.last_success_at,
            message_rate_per_minute=rate,
            latency_ms=self.latency_ms,
            error_count=self.error_count,
            freshness_s=freshness,
            last_error=self.last_error,
        )