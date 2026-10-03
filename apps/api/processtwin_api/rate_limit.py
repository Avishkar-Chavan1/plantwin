from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic


@dataclass
class FixedWindowRateLimiter:
    """Bounded in-process abuse guard for one API process.

    The production runbook still requires an ingress/WAF limit shared across replicas.
    This guard remains useful when the reverse proxy is misconfigured or bypassed.
    """

    _hits: dict[str, deque[float]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def allow(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = monotonic()
        cutoff = now - window_seconds
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                retry_after = max(1, int(window_seconds - (now - hits[0])) + 1)
                return False, retry_after
            hits.append(now)
            return True, 0

    def reset(self) -> None:
        """Clear all rate limit state. For testing only."""
        with self._lock:
            self._hits.clear()


# Global instance for production use; tests should override via dependency injection.
RATE_LIMITER = FixedWindowRateLimiter()
