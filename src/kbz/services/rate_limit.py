"""In-memory sliding-window rate limiter.

Single-process only — intentionally. If/when kbz-api scales horizontally,
swap the internals for Redis (`INCR` + `EXPIRE`). The `RateLimiter`
public surface shouldn't need to change.

Why in-memory, why now:
    The only protected endpoint today is /auth/request-magic-link, which
    is fronted by one uvicorn process. A cross-process limiter would be
    strictly more complex and no more secure for the actual threat
    model (automated spam of magic-link emails = our Resend bill +
    users getting unwanted mail).

Usage:
    limiter = RateLimiter()
    hit = limiter.check(key="email:alice@example.com", limit=5, window_s=3600)
    if not hit.allowed:
        raise HTTPException(429, headers={"Retry-After": str(hit.retry_after_s)})
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass


@dataclass
class RateLimitResult:
    allowed: bool
    # Seconds until the oldest counted hit expires (i.e. when the next
    # request is guaranteed to succeed). Only meaningful when allowed=False.
    retry_after_s: int
    # For observability.
    hits_in_window: int
    limit: int


class RateLimiter:
    """Fixed-window-of-the-last-N-seconds limiter. Threadsafe for a
    single process."""

    # Run a sweep over self._buckets once every N check() calls.
    # Without this the bucket dict grows unboundedly: every distinct
    # key (per-email + per-IP for magic-link, plus any future limiter
    # callers) leaves a deque behind even after its entries expire.
    # On a server that's been up for weeks with thousands of distinct
    # senders this is a slow leak. The sweep is O(buckets) and only
    # fires every _PURGE_EVERY calls, so amortized cost is trivial.
    _PURGE_EVERY = 256

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._calls_since_purge = 0

    def check(self, *, key: str, limit: int, window_s: int) -> RateLimitResult:
        """Record a hit at NOW and report whether the bucket overflows.

        Each call counts as one hit regardless of outcome; that matches
        how a naïve attacker's script behaves (they don't stop just
        because they got 429'd).
        """
        now = time.monotonic()
        cutoff = now - window_s
        with self._lock:
            q = self._buckets.get(key)
            if q is None:
                q = deque()
                self._buckets[key] = q
            # Prune expired hits from the left
            while q and q[0] < cutoff:
                q.popleft()
            # Add this hit
            q.append(now)
            hits = len(q)
            # Opportunistic dict-level purge. Drop any bucket whose
            # newest entry is older than the window we just used —
            # those entries are guaranteed to be expired by now and
            # the bucket holds no useful state.
            self._calls_since_purge += 1
            if self._calls_since_purge >= self._PURGE_EVERY:
                self._calls_since_purge = 0
                stale_keys = [
                    k for k, dq in self._buckets.items()
                    if not dq or dq[-1] < cutoff
                ]
                for k in stale_keys:
                    del self._buckets[k]
            if hits <= limit:
                return RateLimitResult(
                    allowed=True, retry_after_s=0,
                    hits_in_window=hits, limit=limit,
                )
            # Over the limit. Retry-after is when the OLDEST counted hit
            # (q[0]) falls out of the window.
            retry = int(q[0] + window_s - now) + 1
            return RateLimitResult(
                allowed=False, retry_after_s=max(1, retry),
                hits_in_window=hits, limit=limit,
            )

    def forget(self, key: str) -> None:
        """Manual reset — useful in tests."""
        with self._lock:
            self._buckets.pop(key, None)

    def purge_expired(self, *, window_s: int) -> int:
        """Drop buckets whose newest entry is older than window_s. Call
        from a periodic task if memory growth ever becomes a concern;
        at our current scale it won't."""
        now = time.monotonic()
        cutoff = now - window_s
        dropped = 0
        with self._lock:
            for key in list(self._buckets.keys()):
                q = self._buckets[key]
                if not q or q[-1] < cutoff:
                    del self._buckets[key]
                    dropped += 1
        return dropped


# Process-wide singleton. Routes import this directly.
magic_link_limiter = RateLimiter()
