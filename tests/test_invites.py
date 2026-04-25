"""Invite flow tests — create invite, claim invite, Membership proposal filed.

End-to-end via httpx. Also checks the security properties that matter:
unauthenticated users can't create invites, claiming twice fails, bogus
codes 404, and claiming for a nonexistent community fails cleanly.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import create_test_community


async def _login(client, email: str) -> str:
    """Helper — request magic link + verify + return user_id."""
    r = await client.post("/auth/request-magic-link", json={"email": email})
    link = r.json()["link"]
    r = await client.get(link)
    assert r.status_code == 200
    return r.json()["user"]["user_id"]


@pytest.mark.asyncio
async def test_create_invite_requires_auth(client):
    # No session cookie → must be 401
    community_id = str(uuid.uuid4())
    r = await client.post(f"/communities/{community_id}/invites")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_invite_with_login_returns_code(client):
    # Log in as a human, create a real community to invite to
    user_id = await _login(client, "founder@example.com")
    # The founder needs to be a backing user for create_test_community
    community = await create_test_community(client, user_id)

    r = await client.post(f"/communities/{community['id']}/invites")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "code" in body and len(body["code"]) > 10
    assert body["url"] == f"/invite/{body['code']}"
    assert body["expires_at"]


@pytest.mark.asyncio
async def test_create_invite_for_unknown_community_404s(client):
    await _login(client, "founder2@example.com")
    fake = uuid.uuid4()
    r = await client.post(f"/communities/{fake}/invites")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_invite_requires_membership(client):
    """A logged-in user who is NOT a member of the community cannot mint
    invites to it. Otherwise anyone could spam invite codes for any
    community and bypass the social-proof model."""
    founder_id = await _login(client, "real-founder@example.com")
    community = await create_test_community(client, founder_id, name="Members Only")

    # Different user logs in and tries to create an invite
    client.cookies.clear()
    await _login(client, "outsider@example.com")
    r = await client.post(f"/communities/{community['id']}/invites")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_preview_invite_returns_community_name(client):
    user_id = await _login(client, "founder3@example.com")
    community = await create_test_community(client, user_id, name="Reading Circle")

    r = await client.post(f"/communities/{community['id']}/invites")
    code = r.json()["code"]

    r = await client.get(f"/invites/{code}")
    assert r.status_code == 200
    preview = r.json()
    assert preview["community_name"] == "Reading Circle"
    assert preview["claimed"] is False


@pytest.mark.asyncio
async def test_claim_invite_creates_user_and_files_membership_proposal(client):
    # Founder creates the invite
    user_id = await _login(client, "founder4@example.com")
    community = await create_test_community(client, user_id)
    r = await client.post(f"/communities/{community['id']}/invites")
    code = r.json()["code"]

    # Drop founder session — claimer doesn't need to be logged in yet
    client.cookies.clear()

    r = await client.post(
        "/invites/claim",
        json={"invite_code": code, "email": "newbie@example.com"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["community_id"] == community["id"]
    assert body["membership_proposal_id"]
    # Dev-mode verify link should be present
    assert body["verify_link"] and body["verify_link"].startswith("/auth/verify?")

    # The Membership proposal should now be visible on the community's
    # proposal list with status OutThere
    r = await client.get(f"/communities/{community['id']}/proposals")
    assert r.status_code == 200
    proposals = r.json()
    mem = [p for p in proposals if p["proposal_type"] == "Membership"]
    assert len(mem) >= 1
    assert any(p["id"] == body["membership_proposal_id"] for p in mem)


@pytest.mark.asyncio
async def test_claim_twice_rejects_second(client):
    user_id = await _login(client, "founder5@example.com")
    community = await create_test_community(client, user_id)
    r = await client.post(f"/communities/{community['id']}/invites")
    code = r.json()["code"]
    client.cookies.clear()

    r1 = await client.post(
        "/invites/claim", json={"invite_code": code, "email": "a@example.com"}
    )
    assert r1.status_code == 200

    r2 = await client.post(
        "/invites/claim", json={"invite_code": code, "email": "b@example.com"}
    )
    assert r2.status_code == 400
    assert "already claimed" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_claim_dedupes_existing_in_flight_membership(client):
    """A user who already has an in-flight Membership proposal in
    a community must NOT get a second one created when they
    consume an invite for the same community. The invite still
    goes claimed (it's been used), but the returned proposal_id
    points at the existing row and no duplicate appears in the
    community's proposal listing."""
    founder_id = await _login(client, "dedupe-founder@example.com")
    community = await create_test_community(client, founder_id)

    # Create TWO invites for the same community.
    code1 = (await client.post(f"/communities/{community['id']}/invites")).json()["code"]
    code2 = (await client.post(f"/communities/{community['id']}/invites")).json()["code"]
    client.cookies.clear()

    # Same email claims both.
    r1 = await client.post(
        "/invites/claim",
        json={"invite_code": code1, "email": "twice@example.com"},
    )
    assert r1.status_code == 200
    first_pid = r1.json()["membership_proposal_id"]

    r2 = await client.post(
        "/invites/claim",
        json={"invite_code": code2, "email": "twice@example.com"},
    )
    assert r2.status_code == 200
    second_pid = r2.json()["membership_proposal_id"]

    # The second claim must have RETURNED THE SAME proposal id —
    # not minted a new ghost row.
    assert second_pid == first_pid

    # The community must show exactly ONE Membership proposal for
    # this applicant.
    listing = (
        await client.get(
            f"/communities/{community['id']}/proposals",
            params={"proposal_type": "Membership"},
        )
    ).json()
    membership_for_applicant = [
        p for p in listing if p["proposal_text"].startswith("twice")
        or "twice@example.com" in (p.get("proposal_text") or "")
    ]
    assert len(membership_for_applicant) == 1


@pytest.mark.asyncio
async def test_claim_rejects_already_active_member(client):
    """If the email maps to a user who is ALREADY an active member of
    the community the invite is for, the claim must fail with 409 and
    NOT mint a duplicate Membership proposal. Without this guard,
    accepting/rejecting that ghost proposal would also perturb the
    closeness graph for everyone else for no reason."""
    # Founder creates community and gets an invite for the same community.
    founder_email = "already-member@example.com"
    founder_id = await _login(client, founder_email)
    community = await create_test_community(client, founder_id)
    code = (await client.post(f"/communities/{community['id']}/invites")).json()["code"]
    client.cookies.clear()

    # Founder claims the invite for their OWN community.
    r = await client.post(
        "/invites/claim",
        json={"invite_code": code, "email": founder_email},
    )
    assert r.status_code == 409
    assert "already a member" in r.json()["detail"]

    # No duplicate Membership proposal was minted — the founder is
    # already a member, no Membership proposal exists for them.
    listing = (
        await client.get(
            f"/communities/{community['id']}/proposals",
            params={"proposal_type": "Membership"},
        )
    ).json()
    assert listing == []


@pytest.mark.asyncio
async def test_claim_bogus_code_404s(client):
    r = await client.post(
        "/invites/claim",
        json={"invite_code": "not-a-real-code", "email": "x@example.com"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_verify_link_from_invite_grants_session(client):
    """Full end-to-end: invite → claim → verify link → authenticated session."""
    user_id = await _login(client, "founder6@example.com")
    community = await create_test_community(client, user_id)
    r = await client.post(f"/communities/{community['id']}/invites")
    code = r.json()["code"]
    client.cookies.clear()

    r = await client.post(
        "/invites/claim", json={"invite_code": code, "email": "joiner@example.com"}
    )
    verify = r.json()["verify_link"]

    r = await client.get(verify)
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "joiner@example.com"
    assert client.cookies.get("kbz_session") is not None

    # /me should confirm the session
    r = await client.get("/auth/me")
    assert r.json()["user"]["email"] == "joiner@example.com"
