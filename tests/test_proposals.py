import pytest
from tests.conftest import create_test_user, create_test_community


@pytest.mark.asyncio
async def test_create_proposal(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "We believe in open governance",
    })
    assert resp.status_code == 201
    proposal = resp.json()
    assert proposal["proposal_type"] == "AddStatement"
    assert proposal["proposal_status"] == "Draft"
    assert proposal["support_count"] == 0


@pytest.mark.asyncio
async def test_proposal_pitch_round_trip(client):
    """A proposal's `pitch` (the proposer's "why") persists and comes
    back on GET and on the enriched list endpoint — it's a separate
    column from proposal_text, not a prefix/suffix of it."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Open governance is a shared value.",
        "pitch": "We repeatedly argue past each other in pulses. "
                 "Naming 'open governance' as a shared value gives us a "
                 "canonical phrase to point to when we disagree on process.",
    })
    assert resp.status_code == 201
    created = resp.json()
    assert created["pitch"].startswith("We repeatedly argue past each other")
    assert created["proposal_text"] == "Open governance is a shared value."

    # List endpoint should also carry the pitch through enrich().
    rlist = await client.get(f"/communities/{community['id']}/proposals")
    assert rlist.status_code == 200
    rows = rlist.json()
    assert any(p["id"] == created["id"] and p["pitch"].startswith("We repeatedly") for p in rows)


@pytest.mark.asyncio
async def test_proposal_pitch_optional(client):
    """Creating without a pitch is fine — the column is nullable and
    the response field comes back as None. This keeps old clients
    (and legacy rows) from breaking."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "No-pitch proposal",
    })
    assert resp.status_code == 201
    assert resp.json()["pitch"] is None


@pytest.mark.asyncio
async def test_list_proposals_respects_limit_and_offset(client):
    """The list endpoint was unbounded — a community with thousands of
    proposals would dump them all plus run enrichment per row. Paginate
    with limit/offset, defaulting to a generous cap but never unlimited."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])
    for i in range(5):
        await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": user["id"],
            "proposal_type": "AddStatement",
            "proposal_text": f"statement {i}",
        })

    # limit=2 returns only 2 of the 5
    r = await client.get(
        f"/communities/{community['id']}/proposals", params={"limit": 2},
    )
    assert r.status_code == 200
    assert len(r.json()) == 2

    # offset slides the window; limit=2 offset=2 returns the next 2
    r = await client.get(
        f"/communities/{community['id']}/proposals",
        params={"limit": 2, "offset": 2},
    )
    assert len(r.json()) == 2

    # Over-the-top limits are rejected rather than silently letting
    # callers pin the db with one request.
    r = await client.get(
        f"/communities/{community['id']}/proposals", params={"limit": 10000},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invalid_proposal_type(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "InvalidType",
        "proposal_text": "Bad proposal",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_non_member_cannot_propose(client):
    user1 = await create_test_user(client, "founder")
    user2 = await create_test_user(client, "outsider")
    community = await create_test_community(client, user1["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user2["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "I'm not a member",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_submit_proposal(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Create proposal
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Test statement",
    })
    proposal = resp.json()
    assert proposal["proposal_status"] == "Draft"

    # Submit it
    resp = await client.patch(f"/proposals/{proposal['id']}/submit")
    assert resp.status_code == 200
    assert resp.json()["proposal_status"] == "OutThere"


@pytest.mark.asyncio
async def test_support_proposal(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Create and submit proposal
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Support me",
    })
    proposal = resp.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")

    # Add support
    resp = await client.post(f"/proposals/{proposal['id']}/support", json={
        "user_id": user["id"],
    })
    assert resp.status_code == 201

    # Check support count
    resp = await client.get(f"/proposals/{proposal['id']}")
    assert resp.json()["support_count"] == 1

    # Regression: /supporters must return the row, not 500.
    # Prior bug: BotProfile outerjoin referenced Proposal.community_id
    # before Proposal was in the FROM clause, crashing the query.
    resp = await client.get(f"/proposals/{proposal['id']}/supporters")
    assert resp.status_code == 200
    supporters = resp.json()
    assert len(supporters) == 1
    assert supporters[0]["user_id"] == user["id"]


@pytest.mark.asyncio
async def test_duplicate_support_rejected(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "No duplicates",
    })
    proposal = resp.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")

    await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user["id"]})
    resp = await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user["id"]})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_remove_support(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Unsupport me",
    })
    proposal = resp.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")
    await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user["id"]})

    # Remove support
    resp = await client.delete(f"/proposals/{proposal['id']}/support/{user['id']}")
    assert resp.status_code == 200

    # Check count is back to 0
    resp = await client.get(f"/proposals/{proposal['id']}")
    assert resp.json()["support_count"] == 0


@pytest.mark.asyncio
async def test_cannot_support_draft(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Still a draft",
    })
    proposal = resp.json()

    # Try to support while still Draft
    resp = await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user["id"]})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_proposals_by_status(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Create two proposals, submit one
    resp1 = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Draft one",
    })
    resp2 = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Submitted one",
    })
    await client.patch(f"/proposals/{resp2.json()['id']}/submit")

    # List all
    resp = await client.get(f"/communities/{community['id']}/proposals")
    assert len(resp.json()) == 2

    # List only OutThere
    resp = await client.get(f"/communities/{community['id']}/proposals?status=OutThere")
    assert len(resp.json()) == 1
    assert resp.json()[0]["proposal_text"] == "Submitted one"


@pytest.mark.asyncio
async def test_membership_proposal_by_non_member(client):
    """Membership proposals can be created by non-members (they propose themselves).

    Also exercises the app's apply-to-join flow end to end: pitch persists,
    the proposal appears in the community's proposal list, and a second
    duplicate apply is rejected (409) so the UI can show a meaningful
    error instead of silently creating a ghost row.
    """
    user1 = await create_test_user(client, "founder")
    user2 = await create_test_user(client, "applicant")
    community = await create_test_community(client, user1["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user2["id"],
        "proposal_type": "Membership",
        "proposal_text": "applicant applied to join",
        "pitch": "I organise weekly co-op meetings and can onboard newcomers.",
        "val_uuid": user2["id"],
    })
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["pitch"] == "I organise weekly co-op meetings and can onboard newcomers."
    assert str(created["user_id"]) == user2["id"]
    assert str(created["val_uuid"]) == user2["id"]

    # The proposal must show up in the community's proposal list so the
    # viewer + app can render it. This was the exact symptom the user
    # reported: "I saw no new membership proposal in the simulated community."
    listing = (await client.get(f"/communities/{community['id']}/proposals")).json()
    ids = [p["id"] for p in listing]
    assert created["id"] in ids, f"membership proposal {created['id']} missing from list"

    # The applicant must be able to submit their own membership proposal.
    # Otherwise it sits as Draft forever and is invisible to UIs that
    # filter to OutThere/OnTheAir (which is most of them).
    sub = await client.patch(f"/proposals/{created['id']}/submit")
    assert sub.status_code == 200, sub.text
    assert sub.json()["proposal_status"] == "OutThere"

    # Duplicate apply must 409, not silently 201 a second row.
    dup = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user2["id"],
        "proposal_type": "Membership",
        "proposal_text": "applicant applied to join",
        "pitch": "retry",
        "val_uuid": user2["id"],
    })
    assert dup.status_code == 409, dup.text
