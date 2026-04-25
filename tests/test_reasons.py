"""Tests for the deliberation tree (Reason) under a proposal."""
import pytest

from tests.conftest import create_test_community, create_test_user


@pytest.mark.asyncio
async def test_create_top_level_pro_and_con_reasons(client):
    """A member can post both a pro and a con as top-level reasons.
    The list endpoint returns both ordered by stance then created_at."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Land a proposal to argue under.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "we publish weekly digests",
    })
    proposal_id = resp.json()["id"]

    # Top-level pro and con.
    resp = await client.post(f"/proposals/{proposal_id}/reasons", json={
        "user_id": user["id"],
        "stance": "pro",
        "claim_text": "transparency builds trust over time",
    })
    assert resp.status_code == 201
    pro = resp.json()
    assert pro["stance"] == "pro"
    assert pro["parent_reason_id"] is None

    resp = await client.post(f"/proposals/{proposal_id}/reasons", json={
        "user_id": user["id"],
        "stance": "con",
        "claim_text": "writing weekly is real ongoing labor",
    })
    assert resp.status_code == 201
    con = resp.json()
    assert con["stance"] == "con"

    # List returns both.
    resp = await client.get(f"/proposals/{proposal_id}/reasons")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    stances = {r["stance"] for r in rows}
    assert stances == {"pro", "con"}


@pytest.mark.asyncio
async def test_counter_reply_must_take_opposite_stance(client):
    """A counter-reply (parent_reason_id set) MUST be the opposite
    stance of its parent — otherwise the tree devolves into pure
    agreement chains and stops representing real debate."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "we publish weekly digests",
    })
    proposal_id = resp.json()["id"]

    # Top-level PRO.
    resp = await client.post(f"/proposals/{proposal_id}/reasons", json={
        "user_id": user["id"],
        "stance": "pro",
        "claim_text": "transparency builds trust",
    })
    parent_id = resp.json()["id"]

    # Same-stance reply must fail.
    resp = await client.post(f"/proposals/{proposal_id}/reasons", json={
        "user_id": user["id"],
        "stance": "pro",
        "claim_text": "yes and people love it",
        "parent_reason_id": parent_id,
    })
    assert resp.status_code == 400
    assert "opposite stance" in resp.json()["detail"].lower()

    # Opposite-stance reply succeeds.
    resp = await client.post(f"/proposals/{proposal_id}/reasons", json={
        "user_id": user["id"],
        "stance": "con",
        "claim_text": "trust is built by acts not by reports",
        "parent_reason_id": parent_id,
    })
    assert resp.status_code == 201
    assert resp.json()["parent_reason_id"] == parent_id


@pytest.mark.asyncio
async def test_non_member_cannot_post_reason(client):
    """Only members of the proposal's community can argue under it.
    A logged-in stranger gets 403."""
    founder = await create_test_user(client, "founder-r")
    outsider = await create_test_user(client, "outsider-r")
    community = await create_test_community(client, founder["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": founder["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "x",
    })
    proposal_id = resp.json()["id"]

    resp = await client.post(f"/proposals/{proposal_id}/reasons", json={
        "user_id": outsider["id"],
        "stance": "con",
        "claim_text": "I object as a passerby",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reason_404_on_unknown_proposal(client):
    """POST against a bogus proposal_id — 404, not 500."""
    user = await create_test_user(client)
    bogus = "00000000-0000-0000-0000-000000000099"
    resp = await client.post(f"/proposals/{bogus}/reasons", json={
        "user_id": user["id"],
        "stance": "pro",
        "claim_text": "into the void",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invalid_stance_rejected_with_422(client):
    """Stance is constrained to 'pro'|'con' via Pydantic Literal —
    anything else 422s before hitting the service layer."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "x",
    })
    proposal_id = resp.json()["id"]

    resp = await client.post(f"/proposals/{proposal_id}/reasons", json={
        "user_id": user["id"],
        "stance": "neutral",  # not in allow-list
        "claim_text": "shrug",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_empty_claim_rejected(client):
    """An empty claim is silly noise; reject at the schema layer."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "x",
    })
    proposal_id = resp.json()["id"]

    resp = await client.post(f"/proposals/{proposal_id}/reasons", json={
        "user_id": user["id"],
        "stance": "pro",
        "claim_text": "",
    })
    assert resp.status_code == 422
