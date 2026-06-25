"""
Per-key rate limiting. Fixed-window counter keyed by the API key's hash.

InMemoryRateLimiter is complete and correct for a single process; the RateLimiter
protocol lets a shared-backend limiter be substituted without touching callers. The
limit is read from RATE_LIMIT_PER_MIN (via the secret provider) at construction.
"""
from __future__ import annotations

import time
from typing import Protocol

from .secret_provider import get_secret


class RateLimiter(Protocol):
    def allow(self, key: str) -> bool:
        ...

    def remaining(self, key: str) -> int:
        ...


class InMemoryRateLimiter:
    def __init__(self, limit_per_min: "int | None" = None, window_seconds: int = 60):
        if limit_per_min is None:
            limit_per_min = int(get_secret("RATE_LIMIT_PER_MIN", "60"))
        self.limit = int(limit_per_min)
        self.window = window_seconds
        self._buckets: dict[str, tuple[int, int]] = {}   # key -> (window_start, count)

    def _now_window(self) -> int:
        return int(time.time()) // self.window

    def allow(self, key: str) -> bool:
        w = self._now_window()
        start, count = self._buckets.get(key, (w, 0))
        if start != w:
            start, count = w, 0
        if count >= self.limit:
            self._buckets[key] = (start, count)
            return False
        self._buckets[key] = (start, count + 1)
        return True

    def remaining(self, key: str) -> int:
        w = self._now_window()
        start, count = self._buckets.get(key, (w, 0))
        if start != w:
            return self.limit
        return max(0, self.limit - count)
