"""Tests for bot_profile CRUD endpoints (`/users/me/bots[/{community_id}]`).

Covers the config-side of the feature (schema + API). BotRunner
end-to-end behavior isn't unit-tested here — it's exercised by the
live smoke test after deploy.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import create_test_community


async def _login(client, email: str) -> str:
    r = await client.post("/auth/request-magic-link", json={"email": email})
    r = await client.get(r.json()["link"])
    return r.json()["user"]["user_id"]


# ── GET /users/me/bots (empty case) ────────────────────────────────

@pytest.mark.asyncio
async def test_bots_requires_auth(client):
    r = await client.get("/users/me/bots")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_bots_empty_on_fresh_user(client):
    await _login(client, "nobots@example.com")
    r = await client.get("/users/me/bots")
    assert r.status_code == 200
    assert r.json() == []


# ── PUT /users/me/bots/{community_id} — create ─────────────────────

@pytest.mark.asyncio
async def test_put_bot_rejects_non_member(client):
    """You can't activate a bot in a kibbutz you aren't a member of."""
    await _login(client, "outsider@example.com")
    # Someone ELSE creates a community
    client.cookies.clear()
    other_uid = await _login(client, "founder@example.com")
    c = await create_test_community(client, other_uid)
    client.cookies.clear()
    # Log back in as outsider, try to activate a bot
    await _login(client, "outsider@example.com")
    r = await client.put(f"/users/me/bots/{c['id']}", json={})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_put_bot_creates_profile_for_member(client):
    """The founder of a kibbutz is an active member and can activate a bot."""
    uid = await _login(client, "botowner@example.com")
    c = await create_test_community(client, uid, name="Bot Test Kibbutz")

    r = await client.put(
        f"/users/me/bots/{c['id']}",
        json={
            "orientation": "producer",
            "initiative": 8,
            "agreeableness": 4,
            "goals": "Ship the onboarding handbook.",
            "boundaries": "Never propose ThrowOut.",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active"] is True                # default
    assert body["orientation"] == "producer"
    assert body["initiative"] == 8
    assert body["agreeableness"] == 4
    assert body["goals"] == "Ship the onboarding handbook."
    assert body["boundaries"] == "Never propose ThrowOut."
    assert body["approval_mode"] == "autonomous"  # default
    assert body["turn_interval_seconds"] == 300  # default
    assert body["last_turn_at"] is None
    assert body["community_name"] == "Bot Test Kibbutz"


@pytest.mark.asyncio
async def test_put_bot_updates_existing_profile(client):
    """A second PUT changes only the fields provided, leaves others alone."""
    uid = await _login(client, "updater@example.com")
    c = await create_test_community(client, uid)

    await client.put(f"/users/me/bots/{c['id']}", json={
        "orientation": "producer",
        "goals": "Original goals",
    })
    # Update only `goals` — others should survive
    r = await client.put(f"/users/me/bots/{c['id']}", json={
        "goals": "Changed goals",
    })
    body = r.json()
    assert body["goals"] == "Changed goals"
    assert body["orientation"] == "producer"  # preserved


@pytest.mark.asyncio
async def test_put_bot_rejects_invalid_orientation(client):
    uid = await _login(client, "invalid-orientation@example.com")
    c = await create_test_community(client, uid)
    r = await client.put(f"/users/me/bots/{c['id']}", json={
        "orientation": "time_traveler",
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_put_bot_rejects_out_of_range_slider(client):
    uid = await _login(client, "invalid-slider@example.com")
    c = await create_test_community(client, uid)
    r = await client.put(f"/users/me/bots/{c['id']}", json={"initiative": 15})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_put_bot_rejects_invalid_approval_mode(client):
    uid = await _login(client, "invalid-approval@example.com")
    c = await create_test_community(client, uid)
    r = await client.put(f"/users/me/bots/{c['id']}", json={
        "approval_mode": "anarchy",
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_put_bot_rejects_short_cadence(client):
    """turn_interval_seconds must be >= 30 to avoid API-credit thrashing."""
    uid = await _login(client, "fast-bot@example.com")
    c = await create_test_community(client, uid)
    r = await client.put(f"/users/me/bots/{c['id']}", json={
        "turn_interval_seconds": 5,
    })
    assert r.status_code == 400


# ── GET /users/me/bots — populated case ────────────────────────────

@pytest.mark.asyncio
async def test_get_bots_lists_all_my_bots(client):
    uid = await _login(client, "multi-bot@example.com")
    c1 = await create_test_community(client, uid, name="K1")
    c2 = await create_test_community(client, uid, name="K2")
    await client.put(f"/users/me/bots/{c1['id']}", json={"orientation": "producer"})
    await client.put(f"/users/me/bots/{c2['id']}", json={"orientation": "consensus"})

    r = await client.get("/users/me/bots")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    names = {b["community_name"] for b in rows}
    assert names == {"K1", "K2"}


# ── DELETE /users/me/bots/{community_id} ──────────────────────────

@pytest.mark.asyncio
async def test_delete_bot_removes_profile(client):
    uid = await _login(client, "deleter@example.com")
    c = await create_test_community(client, uid)
    await client.put(f"/users/me/bots/{c['id']}", json={})

    r = await client.delete(f"/users/me/bots/{c['id']}")
    assert r.status_code == 204

    rows = (await client.get("/users/me/bots")).json()
    assert rows == []


@pytest.mark.asyncio
async def test_delete_unknown_bot_is_idempotent(client):
    """Deleting a bot you never had shouldn't 500 or 404 — it's a no-op
    so the UI's 'Delete bot' button is safe to double-click."""
    await _login(client, "deletenothing@example.com")
    fake = uuid.uuid4()
    r = await client.delete(f"/users/me/bots/{fake}")
    assert r.status_code == 204


# ── Deactivation without deletion ─────────────────────────────────

@pytest.mark.asyncio
async def test_toggle_active_off_preserves_config(client):
    uid = await _login(client, "toggler@example.com")
    c = await create_test_community(client, uid)
    await client.put(f"/users/me/bots/{c['id']}", json={
        "goals": "I should survive a toggle.",
    })
    r = await client.put(f"/users/me/bots/{c['id']}", json={"active": False})
    assert r.json()["active"] is False
    assert r.json()["goals"] == "I should survive a toggle."
