"""
Full governance workflow integration test.

Simulates a complete community lifecycle:
1. Create community with founder
2. Add 2 more members via membership proposals
3. Add a statement (community constitution)
4. Create an action (sub-committee)
5. Change a governance variable
6. Throw out a member
7. Verify all state at the end
"""
import pytest
from tests.conftest import create_test_user, create_test_community


async def _trigger_pulse(client, community_id, member_ids):
    """Trigger a pulse by having enough members support it."""
    for uid in member_ids:
        resp = await client.post(
            f"/communities/{community_id}/pulses/support",
            json={"user_id": uid},
        )
        if resp.status_code == 201 and resp.json().get("pulse_triggered"):
            return
    # If we get here, no single batch triggered it — that's fine, the pulse may
    # have triggered partway through


async def _create_and_accept(client, community_id, supporter_ids, proposal_data):
    """Create proposal, submit, get supporters, run 2 pulses.
    supporter_ids should include enough members to trigger pulses too."""
    resp = await client.post(f"/communities/{community_id}/proposals", json=proposal_data)
    assert resp.status_code == 201
    proposal_id = resp.json()["id"]

    await client.patch(f"/proposals/{proposal_id}/submit")

    for uid in supporter_ids:
        await client.post(f"/proposals/{proposal_id}/support", json={"user_id": uid})

    # Pulse 1: OutThere → OnTheAir
    await _trigger_pulse(client, community_id, supporter_ids)

    # Pulse 2: OnTheAir → Accepted
    await _trigger_pulse(client, community_id, supporter_ids)

    resp = await client.get(f"/proposals/{proposal_id}")
    return resp.json()


@pytest.mark.asyncio
async def test_full_community_lifecycle(client):
    # === Step 1: Create community ===
    alice = await create_test_user(client, "alice")
    community = await create_test_community(client, alice["id"], "Open Kibbutz")

    # Verify initial state
    resp = await client.get(f"/communities/{community['id']}")
    assert resp.json()["member_count"] == 1
    assert resp.json()["name"] == "Open Kibbutz"

    # === Step 2: Add Bob as member ===
    bob = await create_test_user(client, "bob")
    result = await _create_and_accept(client, community["id"], [alice["id"]], {
        "user_id": bob["id"],
        "proposal_type": "Membership",
        "proposal_text": "Bob wants to join",
        "val_uuid": bob["id"],
    })
    assert result["proposal_status"] == "Accepted"

    resp = await client.get(f"/communities/{community['id']}")
    assert resp.json()["member_count"] == 2

    # === Step 3: Add Carol as member (both Alice and Bob support) ===
    carol = await create_test_user(client, "carol")
    result = await _create_and_accept(client, community["id"], [alice["id"], bob["id"]], {
        "user_id": carol["id"],
        "proposal_type": "Membership",
        "proposal_text": "Carol wants to join",
        "val_uuid": carol["id"],
    })
    assert result["proposal_status"] == "Accepted"

    resp = await client.get(f"/communities/{community['id']}")
    assert resp.json()["member_count"] == 3

    # === Step 4: Add a statement (constitution) ===
    result = await _create_and_accept(client, community["id"], [alice["id"], bob["id"]], {
        "user_id": alice["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "All decisions are made transparently through pulses",
    })
    assert result["proposal_status"] == "Accepted"

    resp = await client.get(f"/communities/{community['id']}/statements")
    assert len(resp.json()) == 1

    # === Step 5: Create an action (sub-committee) ===
    result = await _create_and_accept(client, community["id"], [alice["id"], bob["id"]], {
        "user_id": bob["id"],
        "proposal_type": "AddAction",
        "proposal_text": "Create outreach committee",
        "val_text": "Outreach Committee",
    })
    assert result["proposal_status"] == "Accepted"

    resp = await client.get(f"/communities/{community['id']}/actions")
    assert len(resp.json()) == 1

    resp = await client.get(f"/communities/{community['id']}/children")
    assert len(resp.json()) == 1
    assert resp.json()[0]["name"] == "Outreach Committee"

    # === Step 6: Change a variable ===
    result = await _create_and_accept(client, community["id"], [alice["id"], bob["id"]], {
        "user_id": carol["id"],
        "proposal_type": "ChangeVariable",
        "proposal_text": "ProposalSupport",
        "val_text": "20",
    })
    assert result["proposal_status"] == "Accepted"

    resp = await client.get(f"/communities/{community['id']}/variables")
    assert resp.json()["variables"]["ProposalSupport"] == "20"

    # === Step 7: Throw out Carol ===
    # ThrowOut requires 60%, with 3 members need ceil(3*60/100) = 2 supporters
    result = await _create_and_accept(client, community["id"], [alice["id"], bob["id"]], {
        "user_id": alice["id"],
        "proposal_type": "ThrowOut",
        "proposal_text": "Carol violated community rules",
        "val_uuid": carol["id"],
    })
    assert result["proposal_status"] == "Accepted"

    resp = await client.get(f"/communities/{community['id']}")
    assert resp.json()["member_count"] == 2

    # Verify Carol is no longer in active members
    resp = await client.get(f"/communities/{community['id']}/members")
    member_ids = [m["user_id"] for m in resp.json()]
    assert carol["id"] not in member_ids
    assert alice["id"] in member_ids
    assert bob["id"] in member_ids

    # === Step 8: Verify seniority has accumulated ===
    resp = await client.get(f"/communities/{community['id']}/members")
    for member in resp.json():
        # Alice has been through many pulses, should have high seniority
        assert member["seniority"] > 0

    # === Step 9: Verify pulse history ===
    resp = await client.get(f"/communities/{community['id']}/pulses")
    pulses = resp.json()
    done_pulses = [p for p in pulses if p["status"] == 2]
    assert len(done_pulses) >= 7  # At least 7 proposals × 2 pulses each = 14 pulses, many done
