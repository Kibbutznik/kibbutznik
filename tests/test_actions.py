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
async def test_inactive_community_rejects_new_proposals_and_pulses(client):
    """Once an action is ENDED (its child community goes INACTIVE),
    members shouldn't be able to file fresh proposals there or
    drive its pulse cycle. Without the gate, the closed community
    keeps accepting work indefinitely."""
    user = await create_test_user(client)
    parent = await create_test_community(client, user["id"])

    # Create an action — that mints a child community we'll end.
    resp = await client.post(f"/communities/{parent['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddAction",
        "proposal_text": "Working group",
        "val_text": "WG",
    })
    await _accept_proposal(client, parent["id"], user["id"], resp.json()["id"])
    actions = (await client.get(f"/communities/{parent['id']}/actions")).json()
    action_id = actions[0]["action_id"]

    # End the action.
    resp = await client.post(f"/communities/{parent['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "EndAction",
        "proposal_text": "wrap up",
        "val_uuid": action_id,
    })
    await _accept_proposal(client, parent["id"], user["id"], resp.json()["id"])

    # Filing in the now-INACTIVE child community must 400.
    resp = await client.post(f"/communities/{action_id}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "ghost",
    })
    assert resp.status_code == 400
    assert "not active" in resp.json()["detail"].lower()

    # And pulse-support against it must 400 too.
    resp = await client.post(
        f"/communities/{action_id}/pulses/support",
        json={"user_id": user["id"]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_end_action_stamps_decided_at_on_auto_canceled_inside_proposals(client):
    """When EndAction lands and the executor auto-cancels in-flight
    proposals INSIDE the now-ended community, those proposals must
    have decided_at stamped — otherwise they show up in the audit log
    with NULL decision time and sort wrong (or get treated as legacy
    rows by downstream consumers like the deadlock-pulses metric).
    """
    user = await create_test_user(client)
    parent = await create_test_community(client, user["id"])

    # Create an action.
    resp = await client.post(f"/communities/{parent['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddAction",
        "proposal_text": "Working group",
        "val_text": "WG",
    })
    await _accept_proposal(client, parent["id"], user["id"], resp.json()["id"])
    actions = (await client.get(f"/communities/{parent['id']}/actions")).json()
    action_id = actions[0]["action_id"]

    # File an OutThere proposal INSIDE the action community.
    inside = await client.post(f"/communities/{action_id}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "soon to be auto-canceled",
    })
    inside_pid = inside.json()["id"]
    await client.patch(f"/proposals/{inside_pid}/submit")

    # End the parent's action — executor should auto-cancel the inside proposal.
    resp = await client.post(f"/communities/{parent['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "EndAction",
        "proposal_text": "wrap up",
        "val_uuid": action_id,
    })
    await _accept_proposal(client, parent["id"], user["id"], resp.json()["id"])

    # The inside proposal must now show up in the action community's
    # audit log as Canceled WITH a non-NULL decided_at.
    audit = (await client.get(f"/communities/{action_id}/audit")).json()
    assert isinstance(audit, list)
    matching = [e for e in audit if e["proposal_id"] == inside_pid]
    assert len(matching) == 1, f"expected the canceled proposal in audit, got: {audit}"
    entry = matching[0]
    assert entry["proposal_status"] == "Canceled"
    assert entry["decided_at"] is not None, (
        "decided_at must be stamped when the executor auto-cancels in-flight "
        "proposals inside an ended community — otherwise the audit log "
        "shows them with NULL decision time."
    )


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
async def test_join_action_refuses_inactive_action(client, db):
    """A JoinAction targeting an INACTIVE (already-ended) action
    must NOT add the proposer to the dead sub-community. Pre-fix
    the executor blindly inserted a Member row regardless of
    action.status."""
    import uuid as _uuid
    from kbz.enums import MemberStatus, ProposalStatus, ProposalType
    from kbz.models.action import Action
    from kbz.models.community import Community
    from kbz.models.member import Member
    from kbz.models.proposal import Proposal
    from kbz.services.execution_service import ExecutionService

    founder = await create_test_user(client, "ji-founder")
    joiner = await create_test_user(client, "ji-joiner")
    parent = await create_test_community(client, founder["id"])

    parent_id = _uuid.UUID(parent["id"])
    action_id = _uuid.uuid4()
    db.add(Community(
        id=action_id, parent_id=parent_id, name="dead-action",
        status=2, member_count=0,  # INACTIVE
    ))
    db.add(Action(action_id=action_id, parent_community_id=parent_id, status=2))
    joiner_uuid = _uuid.UUID(joiner["id"])
    db.add(Member(
        community_id=parent_id, user_id=joiner_uuid,
        status=MemberStatus.ACTIVE, seniority=0,
    ))
    await db.commit()

    p = Proposal(
        id=_uuid.uuid4(),
        community_id=parent_id,
        user_id=joiner_uuid,
        proposal_type=ProposalType.JOIN_ACTION,
        proposal_status=ProposalStatus.ACCEPTED,
        proposal_text="join the dead one",
        val_text="",
        val_uuid=action_id,
        age=0,
        support_count=0,
    )
    db.add(p)
    await db.commit()

    await ExecutionService(db).execute_proposal(p)
    await db.commit()

    from sqlalchemy import select as _select
    rows = (
        await db.execute(
            _select(Member).where(
                Member.community_id == action_id,
                Member.user_id == joiner_uuid,
            )
        )
    ).scalars().all()
    assert rows == []


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


@pytest.mark.asyncio
async def test_join_action_refuses_when_proposer_thrown_out_of_parent(client, db):
    """A proposer who got thrown out of the PARENT community must NOT
    be added to the action's sub-community when the JoinAction lands.
    Pre-fix the executor blindly called member_svc.create against
    the action with no parent-membership re-check.

    Test path: drive the executor directly with a forged Accepted
    JoinAction so we sidestep the pulse machinery (which has its own
    cascade behaviors that confound the assertion)."""
    import uuid as _uuid
    from kbz.enums import MemberStatus, ProposalStatus, ProposalType
    from kbz.models.action import Action
    from kbz.models.community import Community
    from kbz.models.member import Member
    from kbz.models.proposal import Proposal
    from kbz.services.execution_service import ExecutionService

    founder = await create_test_user(client, "jrc-founder")
    joiner = await create_test_user(client, "jrc-joiner")
    parent = await create_test_community(client, founder["id"])

    # Hand-build an action under the parent so we don't need to drive
    # AddAction through pulses.
    parent_id = _uuid.UUID(parent["id"])
    action_id = _uuid.uuid4()
    db.add(Community(
        id=action_id, parent_id=parent_id, name="WG-direct",
        status=1, member_count=0,
    ))
    db.add(Action(action_id=action_id, parent_community_id=parent_id, status=1))
    # Joiner is a member of the parent — will be thrown out below.
    joiner_uuid = _uuid.UUID(joiner["id"])
    db.add(Member(
        community_id=parent_id, user_id=joiner_uuid,
        status=MemberStatus.THROWN_OUT, seniority=0,
    ))
    await db.commit()

    # Forge an ACCEPTED JoinAction by joiner against the action.
    p = Proposal(
        id=_uuid.uuid4(),
        community_id=parent_id,
        user_id=joiner_uuid,
        proposal_type=ProposalType.JOIN_ACTION,
        proposal_status=ProposalStatus.ACCEPTED,
        proposal_text="let me on",
        val_text="",
        val_uuid=action_id,
        age=0,
        support_count=0,
    )
    db.add(p)
    await db.commit()

    await ExecutionService(db).execute_proposal(p)
    await db.commit()

    # The thrown-out joiner must NOT have been inserted as a member
    # of the action's sub-community. Pre-fix, member_svc.create
    # would have INSERTed an ACTIVE Member row regardless of
    # parent-membership status.
    from sqlalchemy import select as _select
    rows = (
        await db.execute(
            _select(Member).where(
                Member.community_id == action_id,
                Member.user_id == joiner_uuid,
            )
        )
    ).scalars().all()
    assert rows == [], (
        f"thrown-out parent member should not be added to action; "
        f"got Member rows: {[(r.community_id, r.status) for r in rows]}"
    )
