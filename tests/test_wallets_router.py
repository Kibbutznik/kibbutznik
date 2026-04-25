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
    founder = await create_test_user(client, name="founder3")
    community = (
        await client.post("/communities", json={
            "name": "Generic", "founder_user_id": founder["id"],
        })
    ).json()
    r = await client.get(f"/communities/{community['id']}/wallet")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_community_wallet_200_when_financial(client):
    founder = await create_test_user(client, name="founder4")
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
async def test_payment_request_rejects_non_member(client):
    """Non-members must not be able to file Payment proposals against
    a community they don't belong to."""
    # Founder sets up a financial community
    founder_id = await _login(client, "pr-founder@example.com")
    c = (
        await client.post("/communities", json={
            "name": "Pay Kib",
            "founder_user_id": founder_id,
            "enable_financial": True,
        })
    ).json()
    # Stranger tries to file a payment request against it
    client.cookies.clear()
    await _login(client, "pr-stranger@example.com")
    r = await client.post(
        f"/communities/{c['id']}/payment-request",
        json={"amount": "10"},
    )
    assert r.status_code == 403


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


@pytest.mark.asyncio
async def test_payment_request_notifies_other_members(client):
    """Filing a payment-request must drop a proposal.created
    notification in every OTHER member's inbox — pre-fix the
    shortcut bypassed ProposalService.create entirely so other
    members never heard about payment requests."""
    founder_id = await _login(client, "pay-notif-founder@example.com")
    c = (
        await client.post("/communities", json={
            "name": "PayNotif",
            "founder_user_id": founder_id,
            "enable_financial": True,
        })
    ).json()
    # Land a second member so there's someone to notify.
    client.cookies.clear()
    other_id = await _login(client, "pay-notif-other@example.com")
    resp = await client.post(f"/communities/{c['id']}/proposals", json={
        "user_id": other_id,
        "proposal_type": "Membership",
        "proposal_text": "join",
        "val_uuid": other_id,
    })
    membership_id = resp.json()["id"]
    await client.patch(f"/proposals/{membership_id}/submit")
    client.cookies.clear()
    await _login(client, "pay-notif-founder@example.com")
    await client.post(
        f"/proposals/{membership_id}/support", json={"user_id": founder_id},
    )
    for _ in range(2):
        await client.post(
            f"/communities/{c['id']}/pulses/support",
            json={"user_id": founder_id},
        )

    # Founder files the payment-request.
    r = await client.post(
        f"/communities/{c['id']}/payment-request",
        json={"amount": "25", "pitch": "supplies"},
    )
    assert r.status_code == 200, r.text
    pid = r.json()["proposal_id"]

    # Other member's inbox should carry the proposal.created row.
    client.cookies.clear()
    await _login(client, "pay-notif-other@example.com")
    notes = (await client.get("/users/me/notifications")).json()
    matching = [
        n for n in notes
        if n["kind"] == "proposal.created"
        and n["payload"].get("proposal_id") == pid
    ]
    assert len(matching) == 1, (
        "expected proposal.created in the other member's inbox; "
        f"got: {[n['kind'] for n in notes]}"
    )


@pytest.mark.asyncio
async def test_funding_request_notifies_other_members(client):
    """Same gap as payment-request, on the parent-community side."""
    founder_id = await _login(client, "fund-notif-founder@example.com")
    parent = (
        await client.post("/communities", json={
            "name": "ParentFund",
            "founder_user_id": founder_id,
            "enable_financial": True,
        })
    ).json()
    # Land a second member.
    client.cookies.clear()
    other_id = await _login(client, "fund-notif-other@example.com")
    resp = await client.post(f"/communities/{parent['id']}/proposals", json={
        "user_id": other_id,
        "proposal_type": "Membership",
        "proposal_text": "join",
        "val_uuid": other_id,
    })
    mid = resp.json()["id"]
    await client.patch(f"/proposals/{mid}/submit")
    client.cookies.clear()
    await _login(client, "fund-notif-founder@example.com")
    await client.post(
        f"/proposals/{mid}/support", json={"user_id": founder_id},
    )
    for _ in range(2):
        await client.post(
            f"/communities/{parent['id']}/pulses/support",
            json={"user_id": founder_id},
        )

    # Founder lands an action under the parent.
    add = await client.post(f"/communities/{parent['id']}/proposals", json={
        "user_id": founder_id,
        "proposal_type": "AddAction",
        "proposal_text": "side gig",
        "val_text": "Side",
    })
    add_pid = add.json()["id"]
    await client.patch(f"/proposals/{add_pid}/submit")
    await client.post(f"/proposals/{add_pid}/support", json={"user_id": founder_id})
    for _ in range(2):
        await client.post(
            f"/communities/{parent['id']}/pulses/support",
            json={"user_id": founder_id},
        )
    actions = (await client.get(f"/communities/{parent['id']}/actions")).json()
    action_id = actions[0]["action_id"]

    # Founder files the funding-request.
    r = await client.post(
        f"/actions/{action_id}/funding-request",
        json={"amount": "30", "pitch": "tools"},
    )
    assert r.status_code == 200, r.text
    pid = r.json()["proposal_id"]

    # Other member's inbox should carry the proposal.created.
    client.cookies.clear()
    await _login(client, "fund-notif-other@example.com")
    notes = (await client.get("/users/me/notifications")).json()
    matching = [
        n for n in notes
        if n["kind"] == "proposal.created"
        and n["payload"].get("proposal_id") == pid
    ]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_payment_request_rejects_bad_amounts(client):
    """A bare Decimal() accepts '-5' and 'Infinity' — letting either
    through writes a bogus Payment proposal that the executor will
    later refuse, burning a round slot and polluting support state."""
    user_id = await _login(client, "bad-amt@example.com")
    c = (
        await client.post("/communities", json={
            "name": "bad-amt", "founder_user_id": user_id,
            "enable_financial": True,
        })
    ).json()
    for bad in ("-5", "0", "Infinity", "NaN", "not-a-number"):
        r = await client.post(
            f"/communities/{c['id']}/payment-request",
            json={"amount": bad},
        )
        assert r.status_code == 400, f"amount={bad!r} should 400, got {r.status_code}"


