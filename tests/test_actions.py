import pytest
from tests.conftest import create_test_user, create_test_community


async def _accept_proposal(client, community_id, user_id, proposal_id):
    """Helper: submit, support, and run 2 pulses to accept a proposal."""
    await client.patch(f"/proposals/{proposal_id}/submit")
    await client.post(f"/proposals/{proposal_id}/support", json={"user_id": user_id})
    await client.post(f"/communities/{community_id}/pulses/support", json={"user_id": user_id})
    await client.post(f"/communities/{community_id}/pulses/support", json={"user_id": user_id})


@pytest.mark.asyncio
async def test_create_action_via_proposal(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Propose a new action
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddAction",
        "proposal_text": "Create a marketing committee",
        "val_text": "Marketing Committee",
    })
    await _accept_proposal(client, community["id"], user["id"], resp.json()["id"])

    # Check action exists
    resp = await client.get(f"/communities/{community['id']}/actions")
    actions = resp.json()
    assert len(actions) == 1
    assert actions[0]["status"] == 1

    # Check child community was created
    resp = await client.get(f"/communities/{community['id']}/children")
    children = resp.json()
    assert len(children) == 1
    assert children[0]["name"] == "Marketing Committee"


@pytest.mark.asyncio
async def test_end_action_via_proposal(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Create action
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddAction",
        "proposal_text": "Temp committee",
        "val_text": "Temp",
    })
    await _accept_proposal(client, community["id"], user["id"], resp.json()["id"])

    # Get action ID
    actions = (await client.get(f"/communities/{community['id']}/actions")).json()
    action_id = actions[0]["action_id"]

    # End the action
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "EndAction",
        "proposal_text": "End temp committee",
        "val_uuid": action_id,
    })
    await _accept_proposal(client, community["id"], user["id"], resp.json()["id"])

    # Action list should be empty (only active ones shown)
    resp = await client.get(f"/communities/{community['id']}/actions")
    assert len(resp.json()) == 0
