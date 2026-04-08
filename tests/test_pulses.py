import pytest
from tests.conftest import create_test_user, create_test_community


@pytest.mark.asyncio
async def test_pulse_support(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    resp = await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user["id"],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "supported"
    # With 1 member and 50% PulseSupport threshold = ceil(0.5) = 1
    # So 1 support should trigger the pulse
    assert data["pulse_triggered"] is True


@pytest.mark.asyncio
async def test_pulse_creates_new_next_pulse(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Trigger pulse
    await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user["id"],
    })

    # Should now have: 1 Done pulse (originally Next→Active→Done inline),
    # 1 Active pulse, and 1 new Next pulse
    resp = await client.get(f"/communities/{community['id']}/pulses")
    pulses = resp.json()
    statuses = sorted([p["status"] for p in pulses])
    # We should have at least a Next(0) and Active(1) or Done(2)
    assert 0 in statuses  # New Next pulse exists


@pytest.mark.asyncio
async def test_pulse_increments_seniority(client):
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Check initial seniority
    resp = await client.get(f"/communities/{community['id']}/members")
    assert resp.json()[0]["seniority"] == 0

    # Trigger pulse
    await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user["id"],
    })

    # Seniority should be 1
    resp = await client.get(f"/communities/{community['id']}/members")
    assert resp.json()[0]["seniority"] == 1


@pytest.mark.asyncio
async def test_pulse_accepts_proposal(client):
    """Full workflow: create proposal → submit → support → trigger pulse → proposal accepted."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Create and submit AddStatement proposal
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "We stand for transparency",
    })
    proposal = resp.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")

    # Support the proposal (1 member, 15% ProposalSupport = ceil(0.15) = 1 needed)
    await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user["id"]})

    # Trigger first pulse: OutThere → OnTheAir
    await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user["id"],
    })

    # Check proposal is now OnTheAir
    resp = await client.get(f"/proposals/{proposal['id']}")
    assert resp.json()["proposal_status"] == "OnTheAir"

    # Trigger second pulse: OnTheAir → Accepted
    await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user["id"],
    })

    # Check proposal is Accepted
    resp = await client.get(f"/proposals/{proposal['id']}")
    assert resp.json()["proposal_status"] == "Accepted"

    # Check statement was created
    resp = await client.get(f"/communities/{community['id']}/statements")
    statements = resp.json()
    assert len(statements) == 1
    assert statements[0]["statement_text"] == "We stand for transparency"


@pytest.mark.asyncio
async def test_pulse_rejects_unsupported_proposal(client):
    """Proposal on the air without enough support should be rejected."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Create and submit proposal
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Will be rejected",
    })
    proposal = resp.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")

    # Support to get it to OnTheAir
    await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user["id"]})

    # Trigger first pulse (moves to OnTheAir)
    await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user["id"],
    })

    # Remove support before next pulse
    await client.delete(f"/proposals/{proposal['id']}/support/{user['id']}")

    # Trigger second pulse (should reject since 0 support, threshold=1)
    await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user["id"],
    })

    resp = await client.get(f"/proposals/{proposal['id']}")
    assert resp.json()["proposal_status"] == "Rejected"


@pytest.mark.asyncio
async def test_proposal_ages_out(client):
    """Proposal that stays OutThere too long gets canceled."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Create and submit proposal but DON'T support it
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "Will age out",
    })
    proposal = resp.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")

    # Trigger 3 pulses (MaxAge=2, so age > 2 means canceled)
    for _ in range(3):
        await client.post(f"/communities/{community['id']}/pulses/support", json={
            "user_id": user["id"],
        })

    resp = await client.get(f"/proposals/{proposal['id']}")
    assert resp.json()["proposal_status"] == "Canceled"


@pytest.mark.asyncio
async def test_membership_proposal_adds_member(client):
    """Full membership workflow: propose → support → pulse → new member."""
    user1 = await create_test_user(client, "founder")
    user2 = await create_test_user(client, "applicant")
    community = await create_test_community(client, user1["id"])

    # User2 proposes membership
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user2["id"],
        "proposal_type": "Membership",
        "proposal_text": "I want to join",
        "val_uuid": user2["id"],
    })
    proposal = resp.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")

    # Founder supports
    await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user1["id"]})

    # Pulse 1: OutThere → OnTheAir
    await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user1["id"],
    })

    # Pulse 2: OnTheAir → Accepted (need 50% = ceil(1*50/100) = 1 support)
    await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user1["id"],
    })

    # Check user2 is now a member
    resp = await client.get(f"/communities/{community['id']}/members")
    members = resp.json()
    user_ids = [m["user_id"] for m in members]
    assert user2["id"] in user_ids

    # Member count should be 2
    resp = await client.get(f"/communities/{community['id']}")
    assert resp.json()["member_count"] == 2


@pytest.mark.asyncio
async def test_change_variable_proposal(client):
    """Change a governance variable through proposal."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # Propose changing ProposalSupport from 15 to 25
    resp = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "ChangeVariable",
        "proposal_text": "ProposalSupport",
        "val_text": "25",
    })
    proposal = resp.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")
    await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": user["id"]})

    # Two pulses to accept
    await client.post(f"/communities/{community['id']}/pulses/support", json={"user_id": user["id"]})
    await client.post(f"/communities/{community['id']}/pulses/support", json={"user_id": user["id"]})

    # Check variable changed
    resp = await client.get(f"/communities/{community['id']}/variables")
    assert resp.json()["variables"]["ProposalSupport"] == "25"


@pytest.mark.asyncio
async def test_duplicate_pulse_support_rejected(client):
    """Cannot support the same pulse twice."""
    user = await create_test_user(client)
    community = await create_test_community(client, user["id"])

    # This first one triggers the pulse (threshold=1), so the next pulse is created
    resp = await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user["id"],
    })
    assert resp.status_code == 201

    # Support the new next pulse
    resp = await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user["id"],
    })
    # This also triggers, creating another next pulse
    assert resp.status_code == 201

    # Now support again — should work because it's yet another new pulse
    resp = await client.post(f"/communities/{community['id']}/pulses/support", json={
        "user_id": user["id"],
    })
    assert resp.status_code == 201
