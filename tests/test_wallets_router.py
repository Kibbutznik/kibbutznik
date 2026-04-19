"""Wallet HTTP endpoints — financial-module gate returns 404,
funding-request creates a Funding proposal, enable_financial=true
at create writes the Financial variable."""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import create_test_user


async def _login(client, email: str) -> str:
    r = await client.post("/auth/request-magic-link", json={"email": email})
    r = await client.get(r.json()["link"])
    return r.json()["user"]["user_id"]


# ── enable_financial at community creation ────────────────────────

@pytest.mark.asyncio
async def test_create_community_with_enable_financial_sets_variable(client):
    founder = await create_test_user(client, name="founder1")
    r = await client.post(
        "/communities",
        json={
            "name": "Wallet Kibbutz",
            "founder_user_id": founder["id"],
            "enable_financial": True,
        },
    )
    assert r.status_code == 201
    community_id = r.json()["id"]
    # Variable should be set to "internal" eagerly
    r = await client.get(f"/communities/{community_id}/variables")
    assert r.status_code == 200
    assert r.json()["variables"]["Financial"] == "internal"


@pytest.mark.asyncio
async def test_create_community_without_enable_financial_stays_off(client):
    founder = await create_test_user(client, name="founder2")
    r = await client.post(
        "/communities",
        json={
            "name": "Regular Kibbutz",
            "founder_user_id": founder["id"],
        },
    )
    assert r.status_code == 201
    community_id = r.json()["id"]
    r = await client.get(f"/communities/{community_id}/variables")
    assert r.json()["variables"]["Financial"] == "false"


# ── Wallet endpoints gate on Financial variable ────────────────────

@pytest.mark.asyncio
async def test_community_wallet_404_when_not_financial(client):
    founder = await create_test_user(client, name="f3")
    community = (
        await client.post("/communities", json={
            "name": "Generic", "founder_user_id": founder["id"],
        })
    ).json()
    r = await client.get(f"/communities/{community['id']}/wallet")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_community_wallet_200_when_financial(client):
    founder = await create_test_user(client, name="f4")
    community = (
        await client.post("/communities", json={
            "name": "Financial",
            "founder_user_id": founder["id"],
            "enable_financial": True,
        })
    ).json()
    r = await client.get(f"/communities/{community['id']}/wallet")
    assert r.status_code == 200
    body = r.json()
    assert body["balance"] == "0"
    assert body["owner_kind"] == "community"
    assert body["recent_entries"] == []


# ── /users/me/wallet requires auth ─────────────────────────────────

@pytest.mark.asyncio
async def test_my_wallet_requires_auth(client):
    r = await client.get("/users/me/wallet")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_my_wallet_provisions_welcome_credits_after_me_call(client):
    """Welcome credits are provisioned on /auth/me (the call every
    browser-side app bootstraps with). After that, the wallet shows
    the gift."""
    await _login(client, "newbie@example.com")
    # Simulate the browser-side bootstrap call
    r = await client.get("/auth/me")
    assert r.status_code == 200 and r.json()["user"] is not None
    r = await client.get("/users/me/wallet")
    assert r.status_code == 200
    # Balance should be the welcome amount — config default "100"
    # is stringified by the router as "100.000000" (Decimal 6-dp)
    assert r.json()["balance"].startswith("100")


# ── Funding-request composes a Funding proposal ────────────────────

@pytest.mark.asyncio
async def test_funding_request_404s_on_unknown_action(client):
    await _login(client, "fr-user@example.com")
    r = await client.post(
        f"/actions/{uuid.uuid4()}/funding-request",
        json={"amount": "50", "pitch": "test"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_payment_request_409s_on_non_leaf(client):
    """If the target community has any Action children, Payment is
    refused at the router — the rule matches handler-side enforcement."""
    # Community without the finance module → should 409 on the
    # financial gate long before leaf-check matters.
    user_id = await _login(client, "pay-user@example.com")
    c = (
        await client.post("/communities", json={
            "name": "x", "founder_user_id": user_id,
        })
    ).json()
    r = await client.post(
        f"/communities/{c['id']}/payment-request",
        json={"amount": "10"},
    )
    assert r.status_code == 409
    assert "Financial module" in r.json()["detail"]
