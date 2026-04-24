"""Tests for the human-product backend endpoints:
  GET  /communities                   (browse)
  GET  /users/me/memberships
  GET  /users/me/pending-applications
  GET  /users/me/sent-invites
  PATCH /users/me
  POST /proposals/{id}/withdraw
  Session-match enforcement on write endpoints
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import create_test_community, create_test_user


async def _login(client, email: str) -> str:
    r = await client.post("/auth/request-magic-link", json={"email": email})
    r = await client.get(r.json()["link"])
    assert r.status_code == 200
    return r.json()["user"]["user_id"]


# ── /communities browse ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_communities_default_shows_roots(client):
    # Create one root community
    user = await create_test_user(client)
    c1 = await create_test_community(client, user["id"], name="First Kibbutz")
    r = await client.get("/communities")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()]
    assert "First Kibbutz" in names


@pytest.mark.asyncio
async def test_list_communities_with_search(client):
    user = await create_test_user(client)
    await create_test_community(client, user["id"], name="Onboarding Collective")
    await create_test_community(client, user["id"], name="Writing Circle")
    r = await client.get("/communities", params={"q": "onbo"})
    assert r.status_code == 200
    names = [c["name"] for c in r.json()]
    assert "Onboarding Collective" in names
    assert "Writing Circle" not in names


@pytest.mark.asyncio
async def test_list_communities_pagination(client):
    user = await create_test_user(client)
    for i in range(5):
        await create_test_community(client, user["id"], name=f"K{i}")
    r = await client.get("/communities", params={"limit": 2})
    assert len(r.json()) == 2


# ── /users/me/memberships ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_memberships_requires_auth(client):
    r = await client.get("/users/me/memberships")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_memberships_empty_when_no_community(client):
    await _login(client, "no-memberships@example.com")
    r = await client.get("/users/me/memberships")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_memberships_returns_founder_of_own_community(client):
    """Creating a community auto-makes the founder an active member."""
    founder_id = await _login(client, "founder-test@example.com")
    community = await create_test_community(client, founder_id, name="Founder's Place")
    r = await client.get("/users/me/memberships")
    assert r.status_code == 200
    rows = r.json()
    ids = [m["community_id"] for m in rows]
    assert community["id"] in ids
    mine = next(m for m in rows if m["community_id"] == community["id"])
    assert mine["community_name"] == "Founder's Place"


# ── /users/me/pending-applications ───────────────────────────────

@pytest.mark.asyncio
async def test_pending_applications_tracks_membership_proposals(client):
    """Claim an invite → Membership proposal is OutThere → shows up here."""
    # Founder creates community + invite
    founder_id = await _login(client, "app-founder@example.com")
    community = await create_test_community(client, founder_id, name="Gated")
    inv = await client.post(f"/communities/{community['id']}/invites")
    code = inv.json()["code"]
    client.cookies.clear()

    # Someone else claims
    claim = await client.post(
        "/invites/claim",
        json={"invite_code": code, "email": "applicant@example.com"},
    )
    # Activate their session via verify link
    await client.get(claim.json()["verify_link"])

    r = await client.get("/users/me/pending-applications")
    assert r.status_code == 200
    apps = r.json()
    assert len(apps) == 1
    assert apps[0]["community_name"] == "Gated"
    assert apps[0]["status"] == "OutThere"


# ── /users/me/sent-invites ────────────────────────────────────────

@pytest.mark.asyncio
async def test_sent_invites_tracks_invites_i_created(client):
    founder_id = await _login(client, "inv-sender@example.com")
    community = await create_test_community(client, founder_id)
    inv_resp = await client.post(f"/communities/{community['id']}/invites")
    assert inv_resp.status_code == 200

    r = await client.get("/users/me/sent-invites")
    assert r.status_code == 200
    invites = r.json()
    assert len(invites) == 1
    assert invites[0]["claimed"] is False
    assert invites[0]["community_name"]


@pytest.mark.asyncio
async def test_sent_invites_flips_claimed_after_use(client):
    founder_id = await _login(client, "inv-sender2@example.com")
    community = await create_test_community(client, founder_id)
    code = (await client.post(f"/communities/{community['id']}/invites")).json()["code"]

    # Another user claims (drop session)
    client.cookies.clear()
    await client.post(
        "/invites/claim",
        json={"invite_code": code, "email": "joiner-claim@example.com"},
    )

    # Log founder back in to inspect sent-invites
    await _login(client, "inv-sender2@example.com")
    invites = (await client.get("/users/me/sent-invites")).json()
    assert len(invites) == 1
    assert invites[0]["claimed"] is True


# ── PATCH /users/me ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_me_changes_user_name(client):
    await _login(client, "rename@example.com")
    r = await client.patch("/users/me", json={"user_name": "rename_new"})
    assert r.status_code == 200
    assert r.json()["user_name"] == "rename_new"
    # /auth/me reflects the change
    r = await client.get("/auth/me")
    assert r.json()["user"]["user_name"] == "rename_new"


@pytest.mark.asyncio
async def test_update_me_rejects_short_name(client):
    await _login(client, "badrename@example.com")
    r = await client.patch("/users/me", json={"user_name": "a"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_update_me_collision_409(client):
    """Two users claim the same name → second gets 409."""
    await _login(client, "first@example.com")
    await client.patch("/users/me", json={"user_name": "claimed_name"})
    client.cookies.clear()
    await _login(client, "second@example.com")
    r = await client.patch("/users/me", json={"user_name": "claimed_name"})
    assert r.status_code == 409


# ── POST /proposals/{id}/withdraw ────────────────────────────────

@pytest.mark.asyncio
async def test_withdraw_by_author_cancels_proposal(client):
    founder_id = await _login(client, "withdraw-author@example.com")
    community = await create_test_community(client, founder_id)
    # File an OutThere Membership proposal directly
    proposal_resp = await client.post(
        f"/communities/{community['id']}/proposals",
        json={
            "user_id": founder_id,
            "proposal_type": "AddStatement",
            "proposal_text": "Be nice",
        },
    )
    pid = proposal_resp.json()["id"]

    r = await client.post(
        f"/proposals/{pid}/withdraw",
        json={"user_id": founder_id},
    )
    assert r.status_code == 200
    assert r.json()["proposal_status"] == "Canceled"


@pytest.mark.asyncio
async def test_withdraw_rejects_non_author(client):
    author_id = await _login(client, "withdraw-nonauthor1@example.com")
    community = await create_test_community(client, author_id)
    proposal_resp = await client.post(
        f"/communities/{community['id']}/proposals",
        json={
            "user_id": author_id,
            "proposal_type": "AddStatement",
            "proposal_text": "Author's thought",
        },
    )
    pid = proposal_resp.json()["id"]

    # New user tries to withdraw someone else's proposal
    client.cookies.clear()
    stranger_id = await _login(client, "withdraw-stranger@example.com")
    r = await client.post(
        f"/proposals/{pid}/withdraw",
        json={"user_id": stranger_id},
    )
    # Session enforces user_id match with body, so 403 from that layer
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_withdraw_blocks_session_spoof(client):
    """A logged-in stranger must not be able to pass `{user_id: author}`
    to slip past the proposal.user_id == data.user_id ownership check.
    That's exactly what enforce_session_matches_body is for."""
    author_id = await _login(client, "withdraw-victim@example.com")
    community = await create_test_community(client, author_id)
    proposal_resp = await client.post(
        f"/communities/{community['id']}/proposals",
        json={
            "user_id": author_id,
            "proposal_type": "AddStatement",
            "proposal_text": "Stays up.",
        },
    )
    pid = proposal_resp.json()["id"]

    client.cookies.clear()
    await _login(client, "withdraw-spoofer@example.com")
    # Spoof the author id in the body — old code passed the ownership
    # check (author == author) and cancelled the proposal.
    r = await client.post(
        f"/proposals/{pid}/withdraw",
        json={"user_id": author_id},
    )
    assert r.status_code == 403
    # And the proposal is still alive.
    g = await client.get(f"/proposals/{pid}")
    assert g.json()["proposal_status"] != "Canceled"


# ── Session-enforcement on write endpoints ────────────────────────

@pytest.mark.asyncio
async def test_create_proposal_blocks_when_session_mismatches_body(client):
    """Logged-in user A cannot POST a proposal authored as user B."""
    await _login(client, "sess-a@example.com")
    other_user = await create_test_user(client)   # random user B
    community = await create_test_community(client, other_user["id"])
    r = await client.post(
        f"/communities/{community['id']}/proposals",
        json={
            "user_id": other_user["id"],   # spoof someone else's id
            "proposal_type": "AddStatement",
            "proposal_text": "spoof",
        },
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_proposal_unauthenticated_still_works(client):
    """Agents have NO session cookie; they must still be able to write."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    # No login → no session cookie
    r = await client.post(
        f"/communities/{community['id']}/proposals",
        json={
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": "agent write",
        },
    )
    assert r.status_code in (200, 201), r.text
