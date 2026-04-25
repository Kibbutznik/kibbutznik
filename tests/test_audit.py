"""Tests for /communities/{id}/audit — governance decision provenance."""
import pytest

from tests.conftest import create_test_community, create_test_user


async def _accept_proposal(client, community_id, user_id, proposal_id):
    await client.patch(f"/proposals/{proposal_id}/submit")
    await client.post(f"/proposals/{proposal_id}/support", json={"user_id": user_id})
    await client.post(
        f"/communities/{community_id}/pulses/support",
        json={"user_id": user_id},
    )
    await client.post(
        f"/communities/{community_id}/pulses/support",
        json={"user_id": user_id},
    )


@pytest.mark.asyncio
async def test_audit_lists_accepted_with_supporters(client):
    """An accepted AddStatement shows up on /audit with the right
    decision metadata: status=Accepted, decided_at non-null,
    supporters list contains the supporter, author info populated."""
    user = await create_test_user(client, "audit-author")
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "we publish weekly digests",
        "pitch": "transparency builds trust",
    })
    proposal_id = resp.json()["id"]
    await _accept_proposal(client, community["id"], user["id"], proposal_id)

    resp = await client.get(f"/communities/{community['id']}/audit")
    assert resp.status_code == 200
    rows = resp.json()
    accepted = [r for r in rows if r["proposal_id"] == proposal_id]
    assert len(accepted) == 1
    entry = accepted[0]
    assert entry["proposal_status"] == "Accepted"
    assert entry["proposal_text"] == "we publish weekly digests"
    assert entry["pitch"] == "transparency builds trust"
    assert entry["author_user_id"] == user["id"]
    assert entry["author_user_name"] == "audit-author"
    assert entry["decided_at"] is not None
    sup_ids = [s["user_id"] for s in entry["supporters"]]
    assert user["id"] in sup_ids


@pytest.mark.asyncio
async def test_audit_only_returns_terminal_states(client):
    """Draft / OutThere / OnTheAir proposals must NOT show up on
    /audit — that view is decided rulings only. We verify by filing
    a proposal but never submitting it."""
    user = await create_test_user(client, "audit-draft")
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "still in draft",
    })
    pid = resp.json()["id"]

    resp = await client.get(f"/communities/{community['id']}/audit")
    assert resp.status_code == 200
    assert all(r["proposal_id"] != pid for r in resp.json())


@pytest.mark.asyncio
async def test_audit_filters_by_status(client):
    """`?statuses=Rejected` returns only rejected rows."""
    user = await create_test_user(client, "audit-filter")
    community = await create_test_community(client, user["id"])

    # Accepted proposal.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "accepted one",
    })
    accepted_id = resp.json()["id"]
    await _accept_proposal(client, community["id"], user["id"], accepted_id)

    # Withdrawn → Canceled.
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "withdrawn one",
    })
    canceled_id = resp.json()["id"]
    await client.post(f"/proposals/{canceled_id}/withdraw", json={
        "user_id": user["id"],
    })

    # Filter to Canceled.
    resp = await client.get(
        f"/communities/{community['id']}/audit",
        params={"statuses": "Canceled"},
    )
    rows = resp.json()
    statuses = {r["proposal_status"] for r in rows}
    assert statuses == {"Canceled"}
    ids = {r["proposal_id"] for r in rows}
    assert canceled_id in ids
    assert accepted_id not in ids


@pytest.mark.asyncio
async def test_audit_404_on_unknown_community(client):
    bogus = "00000000-0000-0000-0000-000000000099"
    resp = await client.get(f"/communities/{bogus}/audit")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_audit_decided_at_set_on_pulse_decision(client):
    """`decided_at` is set when a proposal flips terminal — both on
    pulse-decision (Accepted/Rejected) and on Withdraw (Canceled).
    /audit returns it so the dashboard can render "decided 3h ago"."""
    user = await create_test_user(client, "audit-decide-at")
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "decide me",
    })
    pid = resp.json()["id"]
    await _accept_proposal(client, community["id"], user["id"], pid)

    rows = (await client.get(f"/communities/{community['id']}/audit")).json()
    entry = next(r for r in rows if r["proposal_id"] == pid)
    assert entry["decided_at"] is not None
    # decided_at should be at-or-after created_at
    assert entry["decided_at"] >= entry["created_at"]
