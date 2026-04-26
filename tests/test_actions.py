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


@pytest.mark.asyncio
async def test_end_action_cancels_inside_draft_proposals(client):
    """Pre-fix _exec_end_action only auto-canceled in-flight proposals
    in OUT_THERE/ON_THE_AIR. DRAFT proposals filed inside the action
    (but not yet submitted) were left stranded — the create-time gate
    blocks NEW filings into the now-INACTIVE community, so the
    author's existing Drafts had no path to terminal status, kept
    counting against their ProposalRateLimit forever, and showed up
    in the "your in-flight proposals" view as ghosts they couldn't
    submit (the submit gate refuses INACTIVE communities) and
    couldn't cleanly resolve. Now they're auto-canceled with
    decided_at stamped, same as OUT_THERE/ON_THE_AIR siblings."""
    user = await create_test_user(client)
    parent = await create_test_community(client, user["id"])

    # Create an action under the parent.
    resp = await client.post(f"/communities/{parent['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddAction",
        "proposal_text": "Working group",
        "val_text": "WG",
    })
    await _accept_proposal(client, parent["id"], user["id"], resp.json()["id"])
    action_id = (await client.get(f"/communities/{parent['id']}/actions")).json()[0]["action_id"]

    # File a Draft inside the action — never submit it.
    inside = await client.post(f"/communities/{action_id}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "draft inside the action — to be stranded",
    })
    inside_pid = inside.json()["id"]
    # Sanity: it's Draft.
    assert (await client.get(f"/proposals/{inside_pid}")).json()["proposal_status"] == "Draft"

    # End the action.
    resp = await client.post(f"/communities/{parent['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "EndAction",
        "proposal_text": "wrap up",
        "val_uuid": action_id,
    })
    await _accept_proposal(client, parent["id"], user["id"], resp.json()["id"])

    # The inside Draft must now be Canceled with decided_at stamped.
    after = (await client.get(f"/proposals/{inside_pid}")).json()
    assert after["proposal_status"] == "Canceled", (
        f"Draft inside ended community must be auto-canceled; "
        f"got {after['proposal_status']}"
    )


@pytest.mark.asyncio
async def test_submit_refused_for_inactive_community(client):
    """Defense-in-depth: even if a stale browser tab POSTs submit
    AFTER an EndAction has fired, the submit endpoint must refuse
    rather than promote the Draft to OUT_THERE in a now-dead
    community. The general /communities/{id}/proposals create path
    already enforces this — submit() must mirror it.

    Repro shape: race between the executor's auto-cancel sweep and
    the author's submit click. With the gate, even the loser of the
    race surfaces a clean 400 instead of stranding the proposal."""
    import uuid as _uuid
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
    from sqlalchemy import update as _update
    from kbz.enums import CommunityStatus
    from kbz.models.community import Community

    user = await create_test_user(client)
    parent = await create_test_community(client, user["id"])

    # Create an action.
    resp = await client.post(f"/communities/{parent['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddAction",
        "proposal_text": "WG",
        "val_text": "WG",
    })
    await _accept_proposal(client, parent["id"], user["id"], resp.json()["id"])
    action_id = (await client.get(f"/communities/{parent['id']}/actions")).json()[0]["action_id"]

    # File a Draft inside the action.
    inside = await client.post(f"/communities/{action_id}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddStatement",
        "proposal_text": "stale-tab draft",
    })
    inside_pid = inside.json()["id"]

    # Simulate the race: directly mark the action's community
    # INACTIVE (as if EndAction landed but its auto-cancel sweep
    # raced with this submit). DON'T cancel the proposal — we want
    # to test that submit() refuses to promote it.
    from tests.conftest import db_engine  # noqa
    # Use the same db config as the fixture by fetching directly.
    import os
    from kbz.config import settings
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine(settings.test_database_url)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as s:
        await s.execute(
            _update(Community)
            .where(Community.id == _uuid.UUID(action_id))
            .values(status=CommunityStatus.INACTIVE)
        )
        await s.commit()
    await engine.dispose()

    # Now submit must refuse.
    r = await client.patch(f"/proposals/{inside_pid}/submit")
    assert r.status_code == 400, r.text
    assert "not active" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_action_community_gets_seeded_container_and_plan(client):
    """Pre-fix CommunityService.create gated container/Plan seeding
    on `parent_id == ZERO_UUID`, so action communities (created via
    AddAction with parent_id = the parent community) had no
    container at all. Members who joined via JoinAction had nowhere
    to file artifacts — CreateArtifact requires a val_uuid pointing
    at a container, and there wasn't one. The only path to seed an
    action's container was DelegateArtifact from the parent. This
    is what made simulations stall on "empty pulses one by one":
    once an Action was created without delegated work, nothing in
    it could happen.

    Now every community — root or child — gets the same Plan
    artifact at creation time.
    """
    user = await create_test_user(client)
    parent = await create_test_community(client, user["id"])

    # Create an action via accepted AddAction.
    resp = await client.post(f"/communities/{parent['id']}/proposals", json={
        "user_id": user["id"],
        "proposal_type": "AddAction",
        "proposal_text": "Working group with a Plan",
        "val_text": "WG",
    })
    await _accept_proposal(client, parent["id"], user["id"], resp.json()["id"])

    actions = (await client.get(f"/communities/{parent['id']}/actions")).json()
    assert len(actions) == 1
    action_id = actions[0]["action_id"]

    # The action's containers endpoint must return at least one
    # container, and that container must contain a seeded Plan
    # artifact (proposal_id is null because Plans are system-
    # created — see PR #66 for the schema fix that allows this).
    r = await client.get(f"/artifacts/containers/community/{action_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) >= 1, (
        f"action community {action_id} should have a seeded container "
        f"so members can file artifacts; got: {body}"
    )
    artifacts = body[0]["artifacts"]
    seeded = [a for a in artifacts if a["proposal_id"] is None]
    assert len(seeded) == 1, (
        f"expected exactly one seeded Plan artifact (proposal_id=null) "
        f"in the action's container; got: {artifacts}"
    )
    assert "Plan" in seeded[0]["title"] or "plan" in (seeded[0]["content"] or "").lower(), (
        f"seeded artifact should be a Plan; got title={seeded[0]['title']!r} "
        f"content={(seeded[0]['content'] or '')[:80]!r}"
    )
