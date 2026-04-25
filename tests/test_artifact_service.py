"""Unit tests for ArtifactService — the productive layer.

Tests exercise the service directly against the test DB session, sidestepping
the HTTP/proposal/pulse pipeline so we can isolate lifecycle invariants.
"""
import json
import uuid

import pytest

from kbz.enums import (
    ArtifactStatus,
    ContainerStatus,
    ProposalStatus,
    ProposalType,
)
from kbz.models.action import Action
from kbz.models.community import Community
from kbz.models.proposal import Proposal
from kbz.services.artifact_service import (
    ArtifactService,
    ArtifactServiceError,
    parse_ordered_uuid_list,
)


async def _mk_community(db, name="Root") -> Community:
    c = Community(
        id=uuid.uuid4(),
        parent_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        name=name,
        status=1,
        member_count=1,
    )
    db.add(c)
    await db.flush()
    return c


async def _mk_child_action(db, parent: Community, name="Child") -> Community:
    child = Community(
        id=uuid.uuid4(),
        parent_id=parent.id,
        name=name,
        status=1,
        member_count=1,
    )
    db.add(child)
    db.add(Action(action_id=child.id, parent_community_id=parent.id, status=1))
    await db.flush()
    return child


async def _mk_proposal(db, community_id, user_id, ptype=ProposalType.CREATE_ARTIFACT) -> Proposal:
    p = Proposal(
        id=uuid.uuid4(),
        community_id=community_id,
        user_id=user_id,
        proposal_type=ptype,
        proposal_status=ProposalStatus.ACCEPTED,
        proposal_text="",
        val_text="",
        age=0,
        support_count=0,
    )
    db.add(p)
    await db.flush()
    return p


@pytest.mark.asyncio
async def test_create_root_container(db):
    community = await _mk_community(db)
    svc = ArtifactService(db)
    container = await svc.create_root_container(community.id)
    assert container.community_id == community.id
    assert container.status == ContainerStatus.OPEN
    assert container.delegated_from_artifact_id is None


@pytest.mark.asyncio
async def test_create_artifact_happy_path(db):
    community = await _mk_community(db)
    svc = ArtifactService(db)
    container = await svc.create_root_container(community.id)
    user = uuid.uuid4()
    proposal = await _mk_proposal(db, community.id, user)

    artifact = await svc.create_artifact(
        container_id=container.id,
        content="Hello world",
        title="Greeting",
        author_user_id=user,
        proposal_id=proposal.id,
    )
    assert artifact.status == ArtifactStatus.ACTIVE
    assert artifact.prev_artifact_id is None
    assert artifact.community_id == community.id

    arts = await svc.list_artifacts(container.id)
    assert len(arts) == 1


@pytest.mark.asyncio
async def test_edit_artifact_mutates_in_place(db):
    community = await _mk_community(db)
    svc = ArtifactService(db)
    container = await svc.create_root_container(community.id)
    user = uuid.uuid4()
    p1 = await _mk_proposal(db, community.id, user)
    p2 = await _mk_proposal(db, community.id, user, ProposalType.EDIT_ARTIFACT)

    a1 = await svc.create_artifact(container.id, "v1", "T", user, p1.id)
    a2 = await svc.edit_artifact(a1.id, "v2", "T2", user, p2.id)

    # In-place: same row id, content updated, status still ACTIVE
    assert a2.id == a1.id
    assert a2.content == "v2"
    assert a2.title == "T2"
    assert a2.status == ArtifactStatus.ACTIVE
    assert a2.proposal_id == p2.id

    actives = await svc.list_artifacts(container.id)
    assert [a.id for a in actives] == [a1.id]


@pytest.mark.asyncio
async def test_remove_artifact_marks_retired(db):
    community = await _mk_community(db)
    svc = ArtifactService(db)
    container = await svc.create_root_container(community.id)
    user = uuid.uuid4()
    p = await _mk_proposal(db, community.id, user)
    a = await svc.create_artifact(container.id, "x", "", user, p.id)

    await svc.remove_artifact(a.id)
    await db.refresh(a)
    assert a.status == ArtifactStatus.RETIRED
    assert await svc.list_artifacts(container.id) == []


@pytest.mark.asyncio
async def test_exec_edit_artifact_refuses_cross_community_target(db):
    """An accepted EditArtifact in community A whose val_uuid points
    at an artifact in community B must NOT mutate B's artifact.
    Without the executor's per-community guard, A could rewrite B's
    text just by accepting a proposal that carried a foreign val_uuid.
    """
    from kbz.services.execution_service import ExecutionService
    a_comm = await _mk_community(db, "Alpha")
    b_comm = await _mk_community(db, "Bravo")
    user = uuid.uuid4()

    svc = ArtifactService(db)
    b_container = await svc.create_root_container(b_comm.id)
    b_proposal = await _mk_proposal(db, b_comm.id, user)
    b_art = await svc.create_artifact(
        b_container.id, "Bravo's text", "title", user, b_proposal.id,
    )

    # Forge an accepted EditArtifact in A whose val_uuid points at B's row.
    hijack = Proposal(
        id=uuid.uuid4(),
        community_id=a_comm.id,
        user_id=user,
        proposal_type=ProposalType.EDIT_ARTIFACT,
        proposal_status=ProposalStatus.ACCEPTED,
        proposal_text="overwritten by Alpha",
        val_text="hijacked title",
        val_uuid=b_art.id,
        age=0,
        support_count=0,
    )
    db.add(hijack)
    await db.flush()

    await ExecutionService(db).execute_proposal(hijack)
    await db.refresh(b_art)
    assert b_art.content == "Bravo's text"
    assert b_art.title == "title"


@pytest.mark.asyncio
async def test_exec_remove_artifact_refuses_cross_community_target(db):
    """Same guard for RemoveArtifact: A cannot retire B's artifact."""
    from kbz.services.execution_service import ExecutionService
    a_comm = await _mk_community(db, "Alpha2")
    b_comm = await _mk_community(db, "Bravo2")
    user = uuid.uuid4()

    svc = ArtifactService(db)
    b_container = await svc.create_root_container(b_comm.id)
    b_proposal = await _mk_proposal(db, b_comm.id, user)
    b_art = await svc.create_artifact(
        b_container.id, "B content", "B title", user, b_proposal.id,
    )

    hijack = Proposal(
        id=uuid.uuid4(),
        community_id=a_comm.id,
        user_id=user,
        proposal_type=ProposalType.REMOVE_ARTIFACT,
        proposal_status=ProposalStatus.ACCEPTED,
        proposal_text="",
        val_text="",
        val_uuid=b_art.id,
        age=0,
        support_count=0,
    )
    db.add(hijack)
    await db.flush()

    await ExecutionService(db).execute_proposal(hijack)
    await db.refresh(b_art)
    assert b_art.status == ArtifactStatus.ACTIVE


@pytest.mark.asyncio
async def test_delegate_refuses_inactive_action(db):
    """A DelegateArtifact targeting an INACTIVE (already-ended) action
    must NOT mint a new container in the dead community. Pre-fix the
    container was created — pulses don't process INACTIVE communities
    so the work would never get done, and the parent's
    delegated_from_artifact_id pointed at a dead container."""
    from sqlalchemy import update as _update

    parent = await _mk_community(db, "Parent-INA")
    child = await _mk_child_action(db, parent, "Child-INA")
    # Mark the child action INACTIVE.
    await db.execute(
        _update(Action).where(Action.action_id == child.id)
        .values(status=2)  # INACTIVE
    )
    await db.flush()

    svc = ArtifactService(db)
    container = await svc.create_root_container(parent.id)
    user = uuid.uuid4()
    p = await _mk_proposal(db, parent.id, user)
    artifact = await svc.create_artifact(container.id, "delegate me", "T", user, p.id)

    delegating = await _mk_proposal(db, parent.id, user, ProposalType.DELEGATE_ARTIFACT)

    with pytest.raises(ArtifactServiceError) as excinfo:
        await svc.delegate(artifact.id, child.id, delegating)
    assert "INACTIVE" in str(excinfo.value)


@pytest.mark.asyncio
async def test_delegate_requires_direct_child_action(db):
    parent = await _mk_community(db, "Parent")
    child = await _mk_child_action(db, parent, "Child")
    stranger = await _mk_community(db, "Stranger")  # not a child

    svc = ArtifactService(db)
    container = await svc.create_root_container(parent.id)
    user = uuid.uuid4()
    p = await _mk_proposal(db, parent.id, user)
    artifact = await svc.create_artifact(container.id, "delegate me", "T", user, p.id)

    delegating_proposal = await _mk_proposal(db, parent.id, user, ProposalType.DELEGATE_ARTIFACT)

    # Happy path: direct child works.
    new_container = await svc.delegate(artifact.id, child.id, delegating_proposal)
    assert new_container.community_id == child.id
    assert new_container.delegated_from_artifact_id == artifact.id
    assert new_container.status == ContainerStatus.OPEN

    # Negative: stranger community is not a child Action.
    with pytest.raises(ArtifactServiceError):
        await svc.delegate(artifact.id, stranger.id, delegating_proposal)


@pytest.mark.asyncio
async def test_exec_commit_artifact_refuses_cross_community_container(db):
    """An accepted CommitArtifact in community A whose val_uuid
    points at a container in community B must NOT mark B's
    container committed or wipe B's committed_content. Without
    the executor's per-community guard, A could empty B's
    deliverables just by accepting a proposal that carried a
    foreign val_uuid + an empty val_text."""
    from kbz.services.execution_service import ExecutionService
    a_comm = await _mk_community(db, "Alpha-c")
    b_comm = await _mk_community(db, "Bravo-c")
    user = uuid.uuid4()

    svc = ArtifactService(db)
    b_container = await svc.create_root_container(b_comm.id)
    b_proposal = await _mk_proposal(db, b_comm.id, user)
    await svc.create_artifact(
        b_container.id, "B's content", "B title", user, b_proposal.id,
    )

    hijack = Proposal(
        id=uuid.uuid4(),
        community_id=a_comm.id,
        user_id=user,
        proposal_type=ProposalType.COMMIT_ARTIFACT,
        proposal_status=ProposalStatus.ACCEPTED,
        proposal_text="",
        val_text="[]",  # valid JSON empty list — wipes content if reached
        val_uuid=b_container.id,
        age=0,
        support_count=0,
    )
    db.add(hijack)
    await db.flush()

    await ExecutionService(db).execute_proposal(hijack)
    await db.refresh(b_container)
    # B's container is untouched: status still OPEN, no committed_content set.
    assert b_container.status == ContainerStatus.OPEN
    assert b_container.committed_content in (None, "")


@pytest.mark.asyncio
async def test_exec_create_artifact_refuses_cross_community_container(db):
    """An accepted CreateArtifact in community A whose val_uuid
    points at a container in community B must NOT inject a new
    artifact into B's container."""
    from kbz.services.execution_service import ExecutionService
    a_comm = await _mk_community(db, "Alpha-cr")
    b_comm = await _mk_community(db, "Bravo-cr")
    user = uuid.uuid4()

    svc = ArtifactService(db)
    b_container = await svc.create_root_container(b_comm.id)

    hijack = Proposal(
        id=uuid.uuid4(),
        community_id=a_comm.id,
        user_id=user,
        proposal_type=ProposalType.CREATE_ARTIFACT,
        proposal_status=ProposalStatus.ACCEPTED,
        proposal_text="Alpha sneak attack",
        val_text="Hijacked Title",
        val_uuid=b_container.id,
        age=0,
        support_count=0,
    )
    db.add(hijack)
    await db.flush()

    await ExecutionService(db).execute_proposal(hijack)
    artifacts_in_b = await svc.list_artifacts(b_container.id)
    # B's container must contain ZERO artifacts — nothing got
    # injected by A's accepted proposal.
    assert artifacts_in_b == []


@pytest.mark.asyncio
async def test_exec_create_artifact_uses_proposal_text_as_content(db):
    """An accepted CreateArtifact must store proposal_text as the
    new artifact's content. Pre-fix the handler hardcoded content=""
    so agents had to immediately follow up with EditArtifact to fill
    the body — wasted pulse slot and produced empty Artifact rows
    that broke downstream rendering."""
    from kbz.services.execution_service import ExecutionService

    community = await _mk_community(db, "Content")
    svc = ArtifactService(db)
    container = await svc.create_root_container(community.id)
    user = uuid.uuid4()

    p = Proposal(
        id=uuid.uuid4(),
        community_id=community.id,
        user_id=user,
        proposal_type=ProposalType.CREATE_ARTIFACT,
        proposal_status=ProposalStatus.ACCEPTED,
        proposal_text="The drafted body of the artifact lives here.",
        val_text="My Artifact Title",
        val_uuid=container.id,
        age=0,
        support_count=0,
    )
    db.add(p)
    await db.flush()

    await ExecutionService(db).execute_proposal(p)
    artifacts = await svc.list_artifacts(container.id)
    # We seeded a Plan in create_root_container with founder=None so
    # the artifact list filters Plans out automatically. Find the
    # non-Plan we just created.
    non_plan = [a for a in artifacts if not a.is_plan]
    assert len(non_plan) == 1
    a = non_plan[0]
    assert a.title == "My Artifact Title"
    assert a.content == "The drafted body of the artifact lives here."


@pytest.mark.asyncio
async def test_delegate_refuses_foreign_source_artifact(db):
    """ArtifactService.delegate must refuse a source artifact that
    belongs to a community OTHER than delegating_proposal.community_id.
    Without this guard, A could delegate B's artifact into A's
    own child action; the eventual commit would inject a Draft
    EditArtifact into B's queue without B's consent."""
    a_parent = await _mk_community(db, "A-parent")
    a_child = await _mk_child_action(db, a_parent, "A-child")
    b_parent = await _mk_community(db, "B-parent")

    svc = ArtifactService(db)
    user = uuid.uuid4()

    # B's container with a real artifact in it.
    b_container = await svc.create_root_container(b_parent.id)
    b_proposal = await _mk_proposal(db, b_parent.id, user)
    b_artifact = await svc.create_artifact(
        b_container.id, "B's content", "B title", user, b_proposal.id,
    )

    # A's DelegateArtifact proposal targets B's artifact + A's child.
    delegating = await _mk_proposal(
        db, a_parent.id, user, ProposalType.DELEGATE_ARTIFACT,
    )
    with pytest.raises(ArtifactServiceError, match="Source artifact must belong"):
        await svc.delegate(b_artifact.id, a_child.id, delegating)


@pytest.mark.asyncio
async def test_commit_root_container_seals_directly(db):
    community = await _mk_community(db)
    svc = ArtifactService(db)
    container = await svc.create_root_container(community.id)
    user = uuid.uuid4()
    p = await _mk_proposal(db, community.id, user)
    a1 = await svc.create_artifact(container.id, "first", "", user, p.id)
    a2 = await svc.create_artifact(container.id, "second", "", user, p.id)

    result = await svc.commit_container(container.id, [a1.id, a2.id], user)
    assert result is None  # root commit -> no parent proposal
    await db.refresh(container)
    assert container.status == ContainerStatus.COMMITTED
    assert container.committed_content == "first\n\nsecond"


@pytest.mark.asyncio
async def test_commit_delegated_container_creates_parent_proposal(db):
    parent = await _mk_community(db, "Parent")
    child = await _mk_child_action(db, parent, "Child")
    svc = ArtifactService(db)
    parent_container = await svc.create_root_container(parent.id)
    user = uuid.uuid4()
    p = await _mk_proposal(db, parent.id, user)
    parent_artifact = await svc.create_artifact(parent_container.id, "stub", "Chapter", user, p.id)

    delegating = await _mk_proposal(db, parent.id, user, ProposalType.DELEGATE_ARTIFACT)
    child_container = await svc.delegate(parent_artifact.id, child.id, delegating)

    cp = await _mk_proposal(db, child.id, user)
    a1 = await svc.create_artifact(child_container.id, "alpha", "", user, cp.id)
    a2 = await svc.create_artifact(child_container.id, "beta", "", user, cp.id)

    parent_proposal = await svc.commit_container(child_container.id, [a1.id, a2.id], user)
    assert parent_proposal is not None
    assert parent_proposal.community_id == parent.id
    assert parent_proposal.proposal_type == ProposalType.EDIT_ARTIFACT
    assert parent_proposal.proposal_status == ProposalStatus.DRAFT
    assert parent_proposal.val_uuid == parent_artifact.id
    assert parent_proposal.proposal_text == "alpha\n\nbeta"

    await db.refresh(child_container)
    assert child_container.status == ContainerStatus.PENDING_PARENT
    assert child_container.pending_parent_proposal_id == parent_proposal.id

    # While PENDING_PARENT, mutations are frozen.
    with pytest.raises(ArtifactServiceError):
        await svc.create_artifact(child_container.id, "nope", "", user, cp.id)
    with pytest.raises(ArtifactServiceError):
        await svc.edit_artifact(a1.id, "nope", None, user, cp.id)
    with pytest.raises(ArtifactServiceError):
        await svc.remove_artifact(a1.id)
    with pytest.raises(ArtifactServiceError):
        await svc.commit_container(child_container.id, [a1.id], user)


@pytest.mark.asyncio
async def test_committed_container_cleanup_emits_proposal_canceled(db):
    """When _cleanup_committed_container cancels orphan proposals
    (other EditArtifact / RemoveArtifact / etc. targeting the same
    artifacts, plus CreateArtifact / CommitArtifact targeting the
    container itself), each row must fire proposal.canceled so the
    TKG ingestor stamps canceled_at_round on the proposal node.
    Pre-fix (PR #44 added the handler but no emitter at this site)
    these orphans were silently flipped to CANCELED in the DB but
    invisible to the knowledge graph.
    """
    from kbz.services.event_bus import event_bus

    parent = await _mk_community(db, "ParentCleanup")
    svc = ArtifactService(db)
    parent_container = await svc.create_root_container(parent.id)
    user = uuid.uuid4()
    p = await _mk_proposal(db, parent.id, user)
    artifact = await svc.create_artifact(
        parent_container.id, "stub", "title", user, p.id,
    )

    # Seed an orphan EditArtifact targeting that artifact.
    orphan_edit = Proposal(
        id=uuid.uuid4(),
        community_id=parent.id,
        user_id=user,
        proposal_type=ProposalType.EDIT_ARTIFACT.value,
        proposal_status=ProposalStatus.OUT_THERE.value,
        proposal_text="rewrite",
        val_uuid=artifact.id,
        val_text="",
        age=0,
        support_count=0,
    )
    db.add(orphan_edit)
    await db.flush()

    # Subscribe BEFORE the cleanup runs so we catch the emit.
    q = event_bus.subscribe()
    try:
        await svc._cleanup_committed_container(parent_container)
        await db.commit()
        events = []
        while not q.empty():
            events.append(q.get_nowait())
    finally:
        event_bus.unsubscribe(q)

    canceled_events = [
        e for e in events
        if e.event_type == "proposal.canceled"
        and e.data.get("proposal_id") == str(orphan_edit.id)
    ]
    assert len(canceled_events) == 1, (
        f"expected one proposal.canceled for orphan_edit; "
        f"got events: {[(e.event_type, e.data) for e in events]}"
    )


@pytest.mark.asyncio
async def test_cascade_accept_seals_child_container(db):
    parent = await _mk_community(db, "Parent")
    child = await _mk_child_action(db, parent, "Child")
    svc = ArtifactService(db)
    parent_container = await svc.create_root_container(parent.id)
    user = uuid.uuid4()
    p = await _mk_proposal(db, parent.id, user)
    parent_artifact = await svc.create_artifact(parent_container.id, "stub", "T", user, p.id)
    delegating = await _mk_proposal(db, parent.id, user, ProposalType.DELEGATE_ARTIFACT)
    child_container = await svc.delegate(parent_artifact.id, child.id, delegating)
    cp = await _mk_proposal(db, child.id, user)
    a = await svc.create_artifact(child_container.id, "x", "", user, cp.id)
    parent_proposal = await svc.commit_container(child_container.id, [a.id], user)

    await svc.on_parent_proposal_accepted(parent_proposal.id)
    await db.refresh(child_container)
    assert child_container.status == ContainerStatus.COMMITTED


@pytest.mark.asyncio
async def test_cascade_reject_reopens_child_container(db):
    parent = await _mk_community(db, "Parent")
    child = await _mk_child_action(db, parent, "Child")
    svc = ArtifactService(db)
    parent_container = await svc.create_root_container(parent.id)
    user = uuid.uuid4()
    p = await _mk_proposal(db, parent.id, user)
    parent_artifact = await svc.create_artifact(parent_container.id, "stub", "T", user, p.id)
    delegating = await _mk_proposal(db, parent.id, user, ProposalType.DELEGATE_ARTIFACT)
    child_container = await svc.delegate(parent_artifact.id, child.id, delegating)
    cp = await _mk_proposal(db, child.id, user)
    a = await svc.create_artifact(child_container.id, "x", "", user, cp.id)
    parent_proposal = await svc.commit_container(child_container.id, [a.id], user)

    await svc.on_parent_proposal_rejected(parent_proposal.id)
    await db.refresh(child_container)
    assert child_container.status == ContainerStatus.OPEN
    assert child_container.pending_parent_proposal_id is None
    # Members can now mutate again.
    a2 = await svc.create_artifact(child_container.id, "redo", "", user, cp.id)
    assert a2.status == ArtifactStatus.ACTIVE


@pytest.mark.asyncio
async def test_commit_rejects_artifact_not_in_container(db):
    community = await _mk_community(db)
    svc = ArtifactService(db)
    container = await svc.create_root_container(community.id)
    user = uuid.uuid4()
    p = await _mk_proposal(db, community.id, user)
    a = await svc.create_artifact(container.id, "valid", "", user, p.id)

    bogus = uuid.uuid4()
    with pytest.raises(ArtifactServiceError):
        await svc.commit_container(container.id, [a.id, bogus], user)


def test_parse_ordered_uuid_list():
    a, b = uuid.uuid4(), uuid.uuid4()
    raw = json.dumps([str(a), str(b)])
    assert parse_ordered_uuid_list(raw) == [a, b]
    with pytest.raises(ArtifactServiceError):
        parse_ordered_uuid_list("")
    with pytest.raises(ArtifactServiceError):
        parse_ordered_uuid_list("not json")
    with pytest.raises(ArtifactServiceError):
        parse_ordered_uuid_list(json.dumps({"not": "list"}))
    with pytest.raises(ArtifactServiceError):
        parse_ordered_uuid_list(json.dumps(["not-a-uuid"]))
