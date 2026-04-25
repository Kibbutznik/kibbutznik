"""Auth endpoint tests — magic-link issue, verify, session cookie, /me, logout.

Exercises the FastAPI app end-to-end via httpx so we catch cookie-handling
bugs + middleware ordering that a service-level unit test would miss.
"""

from __future__ import annotations

import pytest

# httpx's AsyncClient stores cookies in `.cookies`. Each test constructs
# a fresh `client` fixture → fresh cookie jar.


@pytest.mark.asyncio
async def test_request_magic_link_creates_user_and_returns_link(client):
    r = await client.post(
        "/auth/request-magic-link", json={"email": "alice@example.com"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sent"] is True
    # dev mode should expose the verify URL for easy click-through
    assert body["link"] and body["link"].startswith("/auth/verify?token=")


@pytest.mark.asyncio
async def test_full_magic_link_flow_sets_session_and_me(client):
    # 1. Request a magic link
    r = await client.post(
        "/auth/request-magic-link", json={"email": "bob@example.com"}
    )
    link = r.json()["link"]
    assert link

    # 2. Verify the link → should set a session cookie
    r = await client.get(link)
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["email"] == "bob@example.com"
    assert user["is_human"] is True
    assert client.cookies.get("kbz_session") is not None

    # 3. /auth/me should now return the same user
    r = await client.get("/auth/me")
    assert r.status_code == 200
    me = r.json()["user"]
    assert me is not None
    assert me["user_id"] == user["user_id"]

    # 4. Logout clears the cookie
    r = await client.post("/auth/logout")
    assert r.status_code == 200
    # After logout, /me should report no user
    # (cookie should be cleared by the Set-Cookie header)
    await client.aclose()  # not needed, but ensures clean state


@pytest.mark.asyncio
async def test_verify_twice_fails_second_time(client):
    r = await client.post(
        "/auth/request-magic-link", json={"email": "carol@example.com"}
    )
    link = r.json()["link"]

    r1 = await client.get(link)
    assert r1.status_code == 200

    # Second verify with the SAME link must fail — magic links are one-shot.
    # Clear cookie so we don't succeed via existing session.
    client.cookies.clear()
    r2 = await client.get(link)
    assert r2.status_code == 400
    assert "invalid or expired" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_verify_bogus_token_rejected(client):
    r = await client.get("/auth/verify?token=not-a-real-token")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_me_without_cookie_returns_null_user(client):
    r = await client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["user"] is None


@pytest.mark.asyncio
async def test_magic_link_reuses_existing_user_by_email(client):
    """Two requests for the same email must resolve to the same user_id —
    we mustn't mint a new User row per request."""
    await client.post(
        "/auth/request-magic-link", json={"email": "dave@example.com"}
    )
    r1 = await client.post(
        "/auth/request-magic-link", json={"email": "dave@example.com"}
    )
    link1 = r1.json()["link"]
    # Verify once to reveal the user
    r = await client.get(link1)
    uid_1 = r.json()["user"]["user_id"]

    # Drop session and request + verify ANOTHER link
    client.cookies.clear()
    r2 = await client.post(
        "/auth/request-magic-link", json={"email": "dave@example.com"}
    )
    link2 = r2.json()["link"]
    r = await client.get(link2)
    uid_2 = r.json()["user"]["user_id"]

    assert uid_1 == uid_2


@pytest.mark.asyncio
async def test_verify_with_next_redirects(client):
    """When the email-link handler passes `next=/app/#/dashboard`, the
    endpoint responds with a 303 redirect instead of raw JSON."""
    r = await client.post(
        "/auth/request-magic-link", json={"email": "redir@example.com"}
    )
    link = r.json()["link"]
    # Append next param — httpx doesn't follow redirects by default
    r = await client.get(link + "&next=/app/%23/dashboard", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/app/")
    # Cookie gets set on the redirect response
    assert client.cookies.get("kbz_session") is not None


@pytest.mark.asyncio
async def test_verify_next_rejects_open_redirect(client):
    """`next=//evil.com/…` must fall back to the default dashboard path,
    not let an attacker send users off-site."""
    r = await client.post(
        "/auth/request-magic-link", json={"email": "saferedir@example.com"}
    )
    link = r.json()["link"]
    r = await client.get(link + "&next=//evil.com/phish", follow_redirects=False)
    assert r.status_code == 303
    assert "evil.com" not in r.headers["location"]


@pytest.mark.asyncio
async def test_verify_next_rejects_backslash_bypass(client):
    """Browsers normalize backslash to '/' in Location headers, so a
    candidate like `/\\evil.com/…` decodes to `//evil.com/…` in the
    address bar — an open redirect through the back door. Must be
    rejected alongside the leading-`//` variant.
    """
    r = await client.post(
        "/auth/request-magic-link", json={"email": "safebackslash@example.com"}
    )
    link = r.json()["link"]
    # URL-encoded backslash = %5C
    r = await client.get(link + "&next=/%5Cevil.com/phish", follow_redirects=False)
    assert r.status_code == 303
    assert "evil.com" not in r.headers["location"]


@pytest.mark.asyncio
async def test_verify_expired_with_next_redirects_to_login(client):
    """Expired/invalid token + next param → redirect to /app/#/login
    rather than surfacing a 400 JSON error."""
    r = await client.get(
        "/auth/verify?token=invalid&next=/app/%23/dashboard",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "login" in r.headers["location"]


@pytest.mark.asyncio
async def test_logout_invalidates_cookie_value(client):
    """After logout, the same raw token should not resolve anymore."""
    r = await client.post(
        "/auth/request-magic-link", json={"email": "erin@example.com"}
    )
    await client.get(r.json()["link"])
    # Now we have a cookie. Snapshot it.
    cookie_val = client.cookies.get("kbz_session")
    assert cookie_val

    await client.post("/auth/logout")

    # Re-attach the raw value and hit /me — server should treat it as invalid
    client.cookies.set("kbz_session", cookie_val)
    r = await client.get("/auth/me")
    assert r.json()["user"] is None
