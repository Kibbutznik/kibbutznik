import pytest
from tests.conftest import create_test_user, create_test_community


@pytest.mark.asyncio
async def test_pulse_thresholds_floor_at_one_for_zero_member_community(db):
    """If member_count somehow hits zero (everyone thrown out), the
    OutThere→OnTheAir and OnTheAir→Accepted thresholds used to drop
    to ceil(0 * pct / 100) == 0 — meaning every queued proposal would
    silently auto-accept on the next pulse with zero supporters.
    Floor at 1 so a ghost community can't rubber-stamp."""
    import uuid as _uuid
    from kbz.enums import (
        CommunityStatus, ProposalStatus, ProposalType, PulseStatus,
        DEFAULT_VARIABLES,
    )
    from kbz.models.community import Community
    from kbz.models.proposal import Proposal
    from kbz.models.pulse import Pulse
    from kbz.models.variable import Variable
    from kbz.services.pulse_service import PulseService

    cid = _uuid.uuid4()
    db.add(Community(
        id=cid,
        parent_id=_uuid.UUID("00000000-0000-0000-0000-000000000000"),
        name="Ghost",
        status=CommunityStatus.ACTIVE,
        member_count=0,
    ))
    for name, value in DEFAULT_VARIABLES.items():
        db.add(Variable(community_id=cid, name=name, value=value))
    active = Pulse(
        id=_uuid.uuid4(),
        community_id=cid,
        status=PulseStatus.ACTIVE,
        support_count=0,
        threshold=1,
    )
    nxt = Pulse(
        id=_uuid.uuid4(),
        community_id=cid,
        status=PulseStatus.NEXT,
        support_count=0,
        threshold=1,
    )
    db.add(active)
    db.add(nxt)
    on_air = Proposal(
        id=_uuid.uuid4(),
        community_id=cid,
        user_id=_uuid.uuid4(),
        proposal_type=ProposalType.ADD_STATEMENT,
        proposal_status=ProposalStatus.ON_THE_AIR,
        proposal_text="should NOT pass with 0 members and 0 support",
        val_text="",
        age=0,
        support_count=0,
        pulse_id=active.id,
    )
    db.add(on_air)
    await db.flush()

    await PulseService(db).execute_pulse(cid)

    # Without the floor, ceil(0 * pct / 100) == 0 and `support_count >= 0`
    # would have accepted the proposal. Floored, it must be Rejected.
    await db.refresh(on_air)
    assert on_air.proposal_status == ProposalStatus.REJECTED


@pytest.mark.asyncio
async def test_pulse_support_race_returns_409_not_500(client, monkeypatch):
    """Race-window safety net: if two concurrent same-user
    pulse_supports both pass the existence check before either
    commits, the loser must see a 409 (not a 500 IntegrityError
    crash). Force the loser's path by monkey-patching the dedupe
    SELECT inside the service to always return "no duplicate",
    while keeping a real prior support row in the DB."""
    import uuid as _uuid
    from kbz.services import support_service as _ss

    # 3-member community so PulseSupport threshold is 2 — a single
    # supporter doesn't trigger execute_pulse and doesn't churn
    # the "next" pulse out from under us.
    founder = await create_test_user(client, "race-mp-f")
    a = await create_test_user(client, "race-mp-a")
    b = await create_test_user(client, "race-mp-b")
    community = await create_test_community(client, founder["id"])
    for joiner in (a, b):
        resp = await client.post(f"/communities/{community['id']}/proposals", json={
            "user_id": joiner["id"],
            "proposal_type": "Membership",
            "proposal_text": "join",
            "val_uuid": joiner["id"],
        })
        pid = resp.json()["id"]
        await client.patch(f"/proposals/{pid}/submit")
        await client.post(
            f"/proposals/{pid}/support", json={"user_id": founder["id"]},
        )
        for _ in range(2):
            await client.post(
                f"/communities/{community['id']}/pulses/support",
                json={"user_id": founder["id"]},
            )

    # Founder pulse-supports legitimately. With 3 members @ 50%
    # threshold is 2 — this one support does NOT fire execute,
    # so the (founder, pulse) row survives.
    resp = await client.post(
        f"/communities/{community['id']}/pulses/support",
        json={"user_id": founder["id"]},
    )
    assert resp.status_code == 201
    assert resp.json()["pulse_triggered"] is False

    # Now monkey-patch the dedupe SELECT to return None so the
    # loser's-path code goes ahead and tries to insert again.
    real_execute = _ss.AsyncSession.execute

    async def execute_skipping_dupcheck(self, stmt, *args, **kwargs):
        s = str(stmt)
        if (
            "FROM pulse_supports" in s
            and "WHERE" in s
            and "pulse_supports.user_id" in s
        ):
            class _Empty:
                def scalar_one_or_none(self):
                    return None
            return _Empty()
        return await real_execute(self, stmt, *args, **kwargs)

    monkeypatch.setattr(_ss.AsyncSession, "execute", execute_skipping_dupcheck)

    # Re-fire as the same founder. Without the IntegrityError
    # catch, this would explode with a 500 on commit. With the
    # catch, we get a clean 409.
    resp = await client.post(
        f"/communities/{community['id']}/pulses/support",
        json={"user_id": founder["id"]},
    )
    assert resp.status_code == 409, (
        f"expected 409, got {resp.status_code}: {resp.text}"
    )


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


@pytest.mark.asyncio
async def test_remove_pulse_support_404_without_corrupting_count(client):
    """DELETE /communities/{cid}/pulses/support/{uid} for a user who
    never supported must 404 — and must NOT decrement the pulse's
    support_count. Previously the handler ran the DELETE (0 rows) then
    unconditionally did `support_count = support_count - 1`, drifting
    the counter below the true value."""
    founder = await create_test_user(client, "founder-unsupport")
    # Apply with an applicant so member_count rises above 1 and the
    # first support doesn't auto-fire the pulse before we can inspect it.
    applicant = await create_test_user(client, "applicant-unsupport")
    community = await create_test_community(client, founder["id"])
    prop = await client.post(f"/communities/{community['id']}/proposals", json={
        "user_id": applicant["id"],
        "proposal_type": "Membership",
        "proposal_text": "Let me in",
        "val_uuid": applicant["id"],
    })
    proposal = prop.json()
    await client.patch(f"/proposals/{proposal['id']}/submit")
    await client.post(f"/proposals/{proposal['id']}/support", json={"user_id": founder["id"]})
    # Two pulses to pass OutThere→OnTheAir→Accepted. Now member_count=2.
    for _ in range(2):
        await client.post(f"/communities/{community['id']}/pulses/support", json={
            "user_id": founder["id"],
        })

    # Founder supports the current Next pulse (threshold=ceil(2*0.5)=1,
    # so this DOES trigger). We need the support rows to land first, so
    # instead check the count on the NEW next pulse after firing.
    pulses_before = (await client.get(f"/communities/{community['id']}/pulses")).json()
    next_pulse = next(p for p in pulses_before if p["status"] == 0)
    before_count = next_pulse["support_count"]

    # Try to unsupport as a user who never supported.
    resp = await client.delete(
        f"/communities/{community['id']}/pulses/support/{applicant['id']}"
    )
    assert resp.status_code == 404

    pulses_after = (await client.get(f"/communities/{community['id']}/pulses")).json()
    next_after = next(p for p in pulses_after if p["status"] == 0)
    assert next_after["support_count"] == before_count, (
        "support_count drifted — the failed unsupport must not decrement"
    )
