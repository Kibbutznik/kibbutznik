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


@pytest.mark.asyncio
async def test_end_action_refuses_foreign_action(client):
    """An accepted EndAction in community A whose val_uuid points at
    an action under community B must NOT mark B's action inactive.
    Without the parent guard, A could shut down arbitrary actions in
    B's tree (and sweep the wallet up to A on the way out)."""
    user = await create_test_user(client)
    a = await create_test_community(client, user["id"], name="Alpha")
    b = await create_test_community(client, user["id"], name="Bravo")

    # Land an action under B.
    resp = await client.post(f"/communities/{b['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddAction",
        "proposal_text": "Bravo's action",
        "val_text": "B-Action",
    })
    await _accept_proposal(client, b["id"], user["id"], resp.json()["id"])
    b_action_id = (await client.get(f"/communities/{b['id']}/actions")).json()[0]["action_id"]

    # File EndAction in A pointing at B's action.
    resp = await client.post(f"/communities/{a['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "EndAction",
        "proposal_text": "End B's action from A",
        "val_uuid": b_action_id,
    })
    await _accept_proposal(client, a["id"], user["id"], resp.json()["id"])

    # B's action must still be active.
    b_actions = (await client.get(f"/communities/{b['id']}/actions")).json()
    assert len(b_actions) == 1
    assert b_actions[0]["action_id"] == b_action_id
    assert b_actions[0]["status"] == 1


@pytest.mark.asyncio
async def test_join_action_refuses_foreign_action(client):
    """JoinAction in community A naming an action in B must NOT add
    the proposer to B's action — A has no jurisdiction over B's
    membership."""
    user1 = await create_test_user(client, "founder")
    user2 = await create_test_user(client, "joiner")
    a = await create_test_community(client, user1["id"], name="Alpha-J")
    b = await create_test_community(client, user1["id"], name="Bravo-J")

    # Land an action under B.
    resp = await client.post(f"/communities/{b['id']}/proposals", json={
        "user_id": user1["id"],
        "proposal_type": "AddAction",
        "proposal_text": "Bravo's action",
        "val_text": "B-Action",
    })
    await _accept_proposal(client, b["id"], user1["id"], resp.json()["id"])
    b_action_id = (await client.get(f"/communities/{b['id']}/actions")).json()[0]["action_id"]

    # user2 must be a member of A to be allowed to file the proposal there.
    # Land the membership.
    resp = await client.post(f"/communities/{a['id']}/proposals", json={
        "user_id": user2["id"],
        "proposal_type": "Membership",
        "proposal_text": "join A",
        "val_uuid": user2["id"],
    })
    await _accept_proposal(client, a["id"], user1["id"], resp.json()["id"])

    # File JoinAction in A pointing at B's action.
    resp = await client.post(f"/communities/{a['id']}/proposals", json={
        "user_id": user2["id"],
        "proposal_type": "JoinAction",
        "proposal_text": "join B's action via A",
        "val_uuid": b_action_id,
    })
    await _accept_proposal(client, a["id"], user1["id"], resp.json()["id"])

    # user2 must NOT have ended up as a member of B's action.
    b_action_members = (
        await client.get(f"/communities/{b_action_id}/members")
    ).json()
    member_ids = [m["user_id"] for m in b_action_members]
    assert user2["id"] not in member_ids
