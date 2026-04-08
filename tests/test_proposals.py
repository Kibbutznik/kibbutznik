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
    """Membership proposals can be created by non-members (they propose themselves)."""
    user1 = await create_test_user(client, "founder")
    user2 = await create_test_user(client, "applicant")
    community = await create_test_community(client, user1["id"])

    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user2["id"],
        "proposal_type": "Membership",
        "proposal_text": "I want to join",
        "val_uuid": user2["id"],
    })
    assert resp.status_code == 201
