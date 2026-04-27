"""Tests for the sliding-window rate limiter + its wiring into
/auth/request-magic-link.

Unit tests the limiter directly (no HTTP) for the edge cases, then one
integration test to confirm the auth route actually returns 429 with a
Retry-After header.
"""

from __future__ import annotations


import pytest

from kbz.services.rate_limit import RateLimiter


def test_allows_up_to_limit():
    rl = RateLimiter()
    for i in range(5):
        hit = rl.check(key="k", limit=5, window_s=60)
        assert hit.allowed, f"hit {i} should pass"
        assert hit.hits_in_window == i + 1


def test_blocks_over_limit():
    rl = RateLimiter()
    for _ in range(5):
        rl.check(key="k", limit=5, window_s=60)
    hit = rl.check(key="k", limit=5, window_s=60)
    assert not hit.allowed
    assert hit.retry_after_s >= 1
    assert hit.hits_in_window == 6


def test_keys_are_independent():
    rl = RateLimiter()
    for _ in range(5):
        rl.check(key="a", limit=5, window_s=60)
    # a is at cap, b should still be wide open
    assert rl.check(key="a", limit=5, window_s=60).allowed is False
    assert rl.check(key="b", limit=5, window_s=60).allowed is True


def test_window_expiry_frees_capacity(monkeypatch):
    """Fast-forward monotonic time to simulate a window rolling over."""
    rl = RateLimiter()
    t = [1000.0]
    monkeypatch.setattr(
        "kbz.services.rate_limit.time.monotonic", lambda: t[0]
    )
    for _ in range(5):
        assert rl.check(key="k", limit=5, window_s=60).allowed
    # Over the limit immediately after
    assert not rl.check(key="k", limit=5, window_s=60).allowed
    # Fast-forward past the window — all prior hits expire, bucket resets
    t[0] += 61
    assert rl.check(key="k", limit=5, window_s=60).allowed


def test_forget_clears_bucket():
    rl = RateLimiter()
    for _ in range(5):
        rl.check(key="k", limit=5, window_s=60)
    rl.forget("k")
    assert rl.check(key="k", limit=5, window_s=60).allowed


def test_purge_expired_drops_cold_buckets(monkeypatch):
    rl = RateLimiter()
    t = [1000.0]
    monkeypatch.setattr(
        "kbz.services.rate_limit.time.monotonic", lambda: t[0]
    )
    rl.check(key="cold", limit=5, window_s=60)
    t[0] += 1000
    rl.check(key="warm", limit=5, window_s=60)
    dropped = rl.purge_expired(window_s=60)
    assert dropped == 1
    assert "cold" not in rl._buckets
    assert "warm" in rl._buckets


# ---- integration: actual 429 on /auth/request-magic-link ----


@pytest.mark.asyncio
async def test_request_magic_link_rate_limited_per_email(client):
    """The per-email cap is 5/hour. The 6th hit must 429 with a
    Retry-After header."""
    email = "flood@example.com"
    for i in range(5):
        r = await client.post(
            "/auth/request-magic-link", json={"email": email}
        )
        assert r.status_code == 200, f"hit {i}: {r.text}"
    r = await client.post(
        "/auth/request-magic-link", json={"email": email}
    )
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers.keys()}
    assert int(r.headers["retry-after"]) >= 1


def test_dict_purges_stale_buckets_after_window():
    """Pre-fix the per-key bucket dict grew unboundedly: every distinct
    key (per-email + per-IP for magic-link, plus any future caller)
    left a deque behind even after its entries expired. On a long-
    running server with thousands of distinct senders, slow leak.

    The opportunistic purge runs every _PURGE_EVERY check() calls and
    drops buckets whose newest entry is older than the window. After
    enough activity with stale keys, dict size stays bounded."""
    from kbz.services.rate_limit import RateLimiter
    rl = RateLimiter()
    # Lower the purge threshold so the test doesn't have to spam.
    rl._PURGE_EVERY = 5
    # Seed 10 distinct stale buckets with old entries.
    import time
    fake_old = time.monotonic() - 10_000  # 10000s in the past
    for i in range(10):
        rl._buckets[f"stale_{i}"] = __import__("collections").deque([fake_old])
    assert len(rl._buckets) == 10
    # Now bang on a fresh key. After _PURGE_EVERY calls, the sweep
    # should fire and drop the stale buckets.
    for _ in range(rl._PURGE_EVERY):
        rl.check(key="active", limit=1000, window_s=60)
    # After the sweep: only the active bucket remains. (Stale ones
    # had timestamps far older than the 60s window, so dq[-1] < cutoff.)
    assert "active" in rl._buckets
    assert all(not k.startswith("stale_") for k in rl._buckets), (
        f"expected stale buckets to be purged; still in dict: "
        f"{[k for k in rl._buckets if k.startswith('stale_')]}"
    )


def test_check_does_not_purge_active_buckets():
    """Buckets with a recent entry must NOT be purged even if old
    entries are still present — the deque-level prune handles those.
    The dict-level purge is for ENTIRELY stale buckets."""
    from kbz.services.rate_limit import RateLimiter
    rl = RateLimiter()
    rl._PURGE_EVERY = 3
    rl.check(key="recent_user", limit=10, window_s=60)
    for _ in range(10):
        rl.check(key="other", limit=10, window_s=60)
    assert "recent_user" in rl._buckets, "active bucket got purged"
