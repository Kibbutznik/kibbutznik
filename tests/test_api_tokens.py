"""API-token + bearer-auth tests.

Covers the two contracts external bots depend on:
  1. Authenticated users can mint, list, and revoke personal tokens.
  2. A valid bearer token authenticates requests EXACTLY like a session
     cookie does, and write endpoints enforce that the token's user
     matches the body.user_id.
"""

from __future__ import annotations


import pytest

from tests.conftest import create_test_community


async def _login(client, email: str) -> str:
    r = await client.post("/auth/request-magic-link", json={"email": email})
    r = await client.get(r.json()["link"])
    return r.json()["user"]["user_id"]


# ── Token CRUD ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_token_requires_auth(client):
    r = await client.post("/users/me/tokens", json={"name": "x"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_token_requires_name(client):
    await _login(client, "noname@example.com")
    r = await client.post("/users/me/tokens", json={"name": "  "})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_token_returns_raw_once(client):
    await _login(client, "creator@example.com")
    r = await client.post("/users/me/tokens", json={"name": "my-first"})
    assert r.status_code == 200, r.text
    body = r.json()
    # Raw value is present — this is the ONE time the user sees it
    assert body["token"] and len(body["token"]) > 20
    assert body["name"] == "my-first"
    assert body["id"]
    # Listing must NOT leak the raw value
    r2 = await client.get("/users/me/tokens")
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    assert "token" not in rows[0]
    assert rows[0]["id"] == body["id"]


@pytest.mark.asyncio
async def test_list_tokens_scoped_to_owner(client):
    """User A's tokens must NOT show up for user B."""
    await _login(client, "owner-a@example.com")
    await client.post("/users/me/tokens", json={"name": "A's token"})
    client.cookies.clear()
    await _login(client, "owner-b@example.com")
    rows = (await client.get("/users/me/tokens")).json()
    assert rows == []


@pytest.mark.asyncio
async def test_revoke_token_invalidates_it(client):
    await _login(client, "revoker@example.com")
    created = (await client.post("/users/me/tokens", json={"name": "t"})).json()
    raw = created["token"]

    # Revoke via management endpoint
    r = await client.delete(f"/users/me/tokens/{created['id']}")
    assert r.status_code == 204

    # Bearer requests with the revoked token should fail auth
    client.cookies.clear()
    r = await client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {raw}"},
    )
    # /auth/me is cookie-based; with no cookie AND revoked bearer,
    # returns {user: null}
    assert r.status_code == 200
    assert r.json()["user"] is None


@pytest.mark.asyncio
async def test_revoke_unknown_token_returns_404(client):
    """Revoking a nonexistent token id should 404, not silently 204."""
    await _login(client, "revoke-unknown@example.com")
    r = await client.delete(f"/users/me/tokens/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_revoke_other_users_token_returns_404(client):
    """Revoking someone else's token id returns 404 — callers must not be
    able to probe which token ids exist on other accounts, and must not
    receive a false-success 204."""
    await _login(client, "owner-revoke@example.com")
    created = (await client.post("/users/me/tokens", json={"name": "t"})).json()
    token_id = created["id"]
    # Switch user
    client.cookies.clear()
    await _login(client, "stranger-revoke@example.com")
    r = await client.delete(f"/users/me/tokens/{token_id}")
    assert r.status_code == 404


# ── Bearer auth ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bearer_token_authenticates_like_a_cookie(client):
    await _login(client, "bearer-user@example.com")
    created = (await client.post("/users/me/tokens", json={"name": "mcp"})).json()
    raw = created["token"]
    # Drop cookie; only bearer remains
    client.cookies.clear()

    # /auth/me resolves the user from the bearer header
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "bearer-user@example.com"

    # Protected endpoints accept bearer
    r = await client.get(
        "/users/me/memberships",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_bearer_token_works_on_write_endpoints(client):
    """An external bot script uses the bearer token + body.user_id to
    write. Must succeed when body.user_id matches the token's user."""
    user_id = await _login(client, "writer@example.com")
    created = (await client.post("/users/me/tokens", json={"name": "bot"})).json()
    raw = created["token"]
    community = await create_test_community(client, user_id)
    client.cookies.clear()

    r = await client.post(
        f"/communities/{community['id']}/proposals",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "user_id": user_id,
            "proposal_type": "AddStatement",
            "proposal_text": "Wrote this via my API token.",
        },
    )
    assert r.status_code in (200, 201), r.text


@pytest.mark.asyncio
async def test_bearer_token_cannot_spoof_someone_else(client):
    """Session-match enforcement applies to bearer just like cookies."""
    await _login(client, "impersonator@example.com")
    created = (await client.post("/users/me/tokens", json={"name": "bot"})).json()
    raw = created["token"]
    # Create a SECOND user + their kibbutz
    client.cookies.clear()
    other_uid = await _login(client, "victim@example.com")
    victim_community = await create_test_community(client, other_uid)
    client.cookies.clear()

    # Attacker's bearer token + victim's user_id in body → 403
    r = await client.post(
        f"/communities/{victim_community['id']}/proposals",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "user_id": other_uid,     # spoof the victim
            "proposal_type": "AddStatement",
            "proposal_text": "evil",
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_bearer_unknown_token_returns_unauthenticated(client):
    client.cookies.clear()
    r = await client.get(
        "/auth/me",
        headers={"Authorization": "Bearer not_a_real_token"},
    )
    assert r.status_code == 200
    assert r.json()["user"] is None


@pytest.mark.asyncio
async def test_malformed_bearer_does_not_break_cookie_auth(client):
    """If both a cookie AND a garbled Authorization header are present,
    the cookie should still authenticate the request. Agents and
    browser users coexist."""
    await _login(client, "cohabit@example.com")
    r = await client.get(
        "/users/me/memberships",
        headers={"Authorization": "Bearer "},  # empty after prefix
    )
    assert r.status_code == 200
