"""Tests for the sliding-window rate limiter + its wiring into
/auth/request-magic-link.

Unit tests the limiter directly (no HTTP) for the edge cases, then one
integration test to confirm the auth route actually returns 429 with a
Retry-After header.
"""

from __future__ import annotations

import time

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
