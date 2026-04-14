"""Artifact / ArtifactContainer service.

Implements the productive layer that lives on top of KBZ governance:
  - Artifacts are versioned text contributions inside containers.
  - Containers belong to a community (root or sub-Action).
  - Delegating an artifact creates a fresh container in a child Action.
  - Committing a container concatenates its artifacts in a chosen order
    and (if delegated) bubbles the result up to the parent community as
    a Draft EditArtifact proposal that the parent must still ratify.
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import (
    ArtifactStatus,
    ContainerStatus,
    ProposalStatus,
    ProposalType,
)
from kbz.models.action import Action
from kbz.models.artifact import Artifact
from kbz.models.artifact_container import ArtifactContainer
from kbz.models.proposal import Proposal

logger = logging.getLogger(__name__)


class ArtifactServiceError(Exception):
    """Raised when an artifact operation violates a lifecycle invariant."""


class ArtifactService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------- Container CRUD ----------

    async def create_root_container(
        self,
        community_id: uuid.UUID,
        mission: str | None = None,
        founder_user_id: uuid.UUID | None = None,
    ) -> ArtifactContainer:
        """Seed the primordial container for a root community.

        `mission` is a concrete briefing telling members (and agents) what
        kind of content belongs in this container — what the community is
        actually building. Without it, agents tend to treat CreateArtifact
        like a rephrased AddStatement.
        """
        container = ArtifactContainer(
            id=uuid.uuid4(),
            community_id=community_id,
            delegated_from_artifact_id=None,
            title="Root",
            mission=mission,
            status=ContainerStatus.OPEN,
        )
        self.db.add(container)
        await self.db.flush()

        # Auto-create the Plan artifact
        if founder_user_id:
            await self.create_plan_artifact(container.id, community_id, founder_user_id, mission)

        return container

    async def get_container(self, container_id: uuid.UUID) -> ArtifactContainer | None:
        result = await self.db.execute(
            select(ArtifactContainer).where(ArtifactContainer.id == container_id)
        )
        return result.scalar_one_or_none()

    async def list_containers(self, community_id: uuid.UUID) -> list[ArtifactContainer]:
        result = await self.db.execute(
            select(ArtifactContainer)
            .where(ArtifactContainer.community_id == community_id)
            .order_by(ArtifactContainer.created_at)
        )
        return list(result.scalars().all())

    # ---------- Artifact CRUD ----------

    async def get_artifact(self, artifact_id: uuid.UUID) -> Artifact | None:
        result = await self.db.execute(select(Artifact).where(Artifact.id == artifact_id))
        return result.scalar_one_or_none()

    async def list_artifacts(
        self, container_id: uuid.UUID, *, include_history: bool = False
    ) -> list[Artifact]:
        query = select(Artifact).where(Artifact.container_id == container_id)
        if not include_history:
            query = query.where(Artifact.status == ArtifactStatus.ACTIVE)
        result = await self.db.execute(query.order_by(Artifact.created_at))
        return list(result.scalars().all())

    async def get_history(self, artifact_id: uuid.UUID) -> list[Artifact]:
        """Return the full revision chain ending at `artifact_id`, oldest first.

        Walks `prev_artifact_id` backwards from the given row.
        """
        artifact = await self.get_artifact(artifact_id)
        if not artifact:
            return []
        chain: list[Artifact] = [artifact]
        cur = artifact
        while cur.prev_artifact_id is not None:
            prev = await self.get_artifact(cur.prev_artifact_id)
            if not prev:
                break
            chain.append(prev)
            cur = prev
        chain.reverse()
        return chain

    # ---------- Mutation operations (called from ExecutionService handlers) ----------

    async def create_artifact(
        self,
        container_id: uuid.UUID,
        content: str,
        title: str,
        author_user_id: uuid.UUID,
        proposal_id: uuid.UUID,
    ) -> Artifact:
        container = await self.get_container(container_id)
        if not container:
            raise ArtifactServiceError(f"Container {container_id} not found")
        if container.status != ContainerStatus.OPEN:
            raise ArtifactServiceError(
                f"Container {container_id} is not OPEN (status={container.status})"
            )
        artifact = Artifact(
            id=uuid.uuid4(),
            container_id=container_id,
            community_id=container.community_id,
            title=title or "",
            content=content or "",
            author_user_id=author_user_id,
            proposal_id=proposal_id,
            prev_artifact_id=None,
            status=ArtifactStatus.ACTIVE,
        )
        self.db.add(artifact)
        await self.db.flush()
        return artifact

    async def create_plan_artifact(
        self,
        container_id: uuid.UUID,
        community_id: uuid.UUID,
        author_user_id: uuid.UUID,
        mission: str | None = None,
    ) -> Artifact:
        """Auto-create the Plan artifact for a new container."""
        template = "## Plan\n\n"
        if mission:
            template += f"**Mission:** {mission}\n\n"
        template += (
            "### Goals\n- (What is this community/container trying to produce?)\n\n"
            "### Artifacts Needed\n- (What sections/pieces should be created?)\n\n"
            "### Approach\n- (How should the work be divided and sequenced?)\n"
        )
        artifact = Artifact(
            id=uuid.uuid4(),
            container_id=container_id,
            community_id=community_id,
            title="Plan",
            content=template,
            author_user_id=author_user_id,
            proposal_id=None,
            prev_artifact_id=None,
            status=ArtifactStatus.ACTIVE,
            is_plan=True,
        )
        self.db.add(artifact)
        await self.db.flush()
        return artifact

    async def edit_artifact(
        self,
        artifact_id: uuid.UUID,
        new_content: str,
        new_title: str | None,
        author_user_id: uuid.UUID,
        proposal_id: uuid.UUID,
    ) -> Artifact:
        old = await self.get_artifact(artifact_id)
        if not old:
            raise ArtifactServiceError(f"Artifact {artifact_id} not found")
        if old.status != ArtifactStatus.ACTIVE:
            raise ArtifactServiceError(
                f"Artifact {artifact_id} is not ACTIVE — cannot edit a non-head version"
            )
        container = await self.get_container(old.container_id)
        if not container:
            raise ArtifactServiceError(f"Container {old.container_id} not found")
        if container.status == ContainerStatus.PENDING_PARENT:
            raise ArtifactServiceError(
                f"Container {old.container_id} is PENDING_PARENT — wait for parent community verdict before editing"
            )
        # Plan artifacts are living documents — edited IN PLACE so the single
        # Plan row persists through the container's lifetime until CommitArtifact.
        # No new version row, no SUPERSEDED, no prev_artifact_id chain.
        if old.is_plan:
            if new_content is not None:
                old.content = new_content
            if new_title is not None:
                old.title = new_title
            # Track latest editor + proposal that approved the edit
            old.author_user_id = author_user_id
            old.proposal_id = proposal_id
            await self.db.flush()
            if container.status == ContainerStatus.COMMITTED:
                container.status = ContainerStatus.OPEN
                await self.db.flush()
            return old

        new = Artifact(
            id=uuid.uuid4(),
            container_id=old.container_id,
            community_id=old.community_id,
            title=new_title if new_title is not None else old.title,
            content=new_content if new_content is not None else old.content,
            author_user_id=author_user_id,
            proposal_id=proposal_id,
            prev_artifact_id=old.id,
            status=ArtifactStatus.ACTIVE,
        )
        self.db.add(new)
        old.status = ArtifactStatus.SUPERSEDED
        await self.db.flush()
        # If the container was COMMITTED, reopen it so the community can re-commit
        # with the updated content.
        if container.status == ContainerStatus.COMMITTED:
            container.status = ContainerStatus.OPEN
            await self.db.flush()
        return new

    async def remove_artifact(self, artifact_id: uuid.UUID) -> None:
        artifact = await self.get_artifact(artifact_id)
        if not artifact:
            raise ArtifactServiceError(f"Artifact {artifact_id} not found")
        if artifact.status != ArtifactStatus.ACTIVE:
            raise ArtifactServiceError(
                f"Artifact {artifact_id} is not ACTIVE — already superseded or retired"
            )
        if artifact.is_plan:
            raise ArtifactServiceError(
                f"Artifact {artifact_id} is a Plan — Plans cannot be removed, only edited"
            )
        container = await self.get_container(artifact.container_id)
        if not container or container.status != ContainerStatus.OPEN:
            raise ArtifactServiceError(
                f"Container {artifact.container_id} is not OPEN — removals are frozen"
            )
        artifact.status = ArtifactStatus.RETIRED
        await self.db.flush()

    async def delegate(
        self,
        source_artifact_id: uuid.UUID,
        target_action_community_id: uuid.UUID,
        delegating_proposal: Proposal,
    ) -> ArtifactContainer:
        source = await self.get_artifact(source_artifact_id)
        if not source:
            raise ArtifactServiceError(f"Source artifact {source_artifact_id} not found")
        if source.status != ArtifactStatus.ACTIVE:
            raise ArtifactServiceError(
                f"Source artifact {source_artifact_id} is not ACTIVE"
            )
        if source.is_plan:
            raise ArtifactServiceError(
                f"Source artifact {source_artifact_id} is a Plan — Plans cannot be delegated"
            )

        # Validate target is a direct child Action of the proposing community.
        result = await self.db.execute(
            select(Action).where(Action.action_id == target_action_community_id)
        )
        action = result.scalar_one_or_none()
        if not action:
            raise ArtifactServiceError(
                f"Target community {target_action_community_id} is not a registered Action"
            )
        if action.parent_community_id != delegating_proposal.community_id:
            raise ArtifactServiceError(
                "Delegation target must be a direct child Action of the proposing community"
            )

        # Copy the delegated artifact's content as the child container's
        # mission briefing, so sub-Actions inherit a concrete sense of what
        # to produce instead of starting blank.
        inherited_mission = None
        if source.content:
            inherited_mission = (
                f"This container was delegated from the parent community. "
                f"Expand, refine, or subdivide the following content into "
                f"concrete artifacts that, when committed, will replace the "
                f"original in the parent:\n\n{source.content}"
            )

        container = ArtifactContainer(
            id=uuid.uuid4(),
            community_id=target_action_community_id,
            delegated_from_artifact_id=source.id,
            title=source.title or "Delegated work",
            mission=inherited_mission,
            status=ContainerStatus.OPEN,
        )
        self.db.add(container)
        await self.db.flush()

        # Auto-create the Plan artifact in the delegated container
        await self.create_plan_artifact(
            container.id, target_action_community_id,
            delegating_proposal.user_id, inherited_mission,
        )

        return container

    async def commit_container(
        self,
        container_id: uuid.UUID,
        ordered_artifact_ids: list[uuid.UUID],
        committer_user_id: uuid.UUID,
    ) -> Proposal | None:
        """Commit a container.

        Returns the auto-generated parent EditArtifact proposal (if delegated),
        or None for root containers.
        """
        container = await self.get_container(container_id)
        if not container:
            raise ArtifactServiceError(f"Container {container_id} not found")
        if container.status != ContainerStatus.OPEN:
            raise ArtifactServiceError(
                f"Container {container_id} is not OPEN — already pending or committed"
            )

        # Validate every id is an ACTIVE artifact in this container.
        # Plan artifacts are excluded — they guide work but are not deliverables.
        active_artifacts = await self.list_artifacts(container_id, include_history=False)
        active_by_id = {a.id: a for a in active_artifacts if not a.is_plan}
        ordered: list[Artifact] = []
        for aid in ordered_artifact_ids:
            # Silently skip Plan artifacts if included
            art = active_by_id.get(aid)
            if not art:
                # Check if it's a plan artifact being accidentally included
                plan_check = {a.id for a in active_artifacts if a.is_plan}
                if aid in plan_check:
                    continue  # skip plan silently
                raise ArtifactServiceError(
                    f"Artifact {aid} is not an ACTIVE member of container {container_id}"
                )
            ordered.append(art)

        unified = "\n\n".join(a.content for a in ordered)
        container.committed_content = unified
        container.committed_at = datetime.now(timezone.utc)

        # Root container? straight to COMMITTED, emit completion.
        if container.delegated_from_artifact_id is None:
            container.status = ContainerStatus.COMMITTED
            await self.db.flush()
            from kbz.services.event_bus import event_bus
            await event_bus.emit(
                "community.completed",
                community_id=container.community_id,
                user_id=committer_user_id,
                container_id=container.id,
            )
            return None

        # Delegated container: create a Draft EditArtifact in the parent community.
        source = await self.get_artifact(container.delegated_from_artifact_id)
        if not source:
            raise ArtifactServiceError(
                f"Cannot find originating artifact {container.delegated_from_artifact_id}"
            )
        parent_proposal = Proposal(
            id=uuid.uuid4(),
            community_id=source.community_id,
            user_id=committer_user_id,
            proposal_type=ProposalType.EDIT_ARTIFACT,
            proposal_status=ProposalStatus.DRAFT,
            proposal_text=unified,
            val_uuid=source.id,
            val_text=source.title or "",
            age=0,
            support_count=0,
        )
        self.db.add(parent_proposal)
        container.status = ContainerStatus.PENDING_PARENT
        container.pending_parent_proposal_id = parent_proposal.id
        await self.db.flush()
        return parent_proposal

    # ---------- Cascade event handlers ----------

    async def on_parent_proposal_accepted(self, parent_proposal_id: uuid.UUID) -> None:
        """Called by event subscriber when a 'proposal.accepted' event fires.

        If the accepted proposal is the auto-generated EditArtifact for some
        sub-action's pending container, flip that container to COMMITTED and
        cascade-cleanup: retire artifacts, cancel orphaned proposals, and
        recursively seal any sub-containers delegated further downstream.
        """
        result = await self.db.execute(
            select(ArtifactContainer).where(
                ArtifactContainer.pending_parent_proposal_id == parent_proposal_id
            )
        )
        container = result.scalar_one_or_none()
        if not container:
            return
        container.status = ContainerStatus.COMMITTED
        container.committed_at = datetime.now(timezone.utc)

        # --- Cascade cleanup ---
        await self._cleanup_committed_container(container)

        await self.db.flush()
        logger.info(
            "ArtifactService: container %s sealed (parent proposal %s accepted)",
            container.id,
            parent_proposal_id,
        )

    async def _cleanup_committed_container(self, container: ArtifactContainer) -> None:
        """Retire artifacts, cancel orphaned proposals, and cascade to sub-containers."""
        active_statuses = (ProposalStatus.OUT_THERE.value, ProposalStatus.ON_THE_AIR.value)
        artifact_types = [
            ProposalType.EDIT_ARTIFACT.value,
            ProposalType.REMOVE_ARTIFACT.value,
            ProposalType.DELEGATE_ARTIFACT.value,
            ProposalType.CREATE_ARTIFACT.value,
            ProposalType.COMMIT_ARTIFACT.value,
        ]

        # 1. Retire all ACTIVE artifacts in this container
        active_arts = await self.list_artifacts(container.id, include_history=False)
        artifact_ids = [a.id for a in active_arts]
        for art in active_arts:
            art.status = ArtifactStatus.RETIRED

        if not artifact_ids:
            return

        # 2. Cancel active proposals referencing these artifacts
        #    (EditArtifact, RemoveArtifact, DelegateArtifact use val_uuid = artifact_id)
        orphan_result = await self.db.execute(
            select(Proposal).where(
                Proposal.community_id == container.community_id,
                Proposal.proposal_status.in_(active_statuses),
                Proposal.proposal_type.in_([
                    ProposalType.EDIT_ARTIFACT.value,
                    ProposalType.REMOVE_ARTIFACT.value,
                    ProposalType.DELEGATE_ARTIFACT.value,
                ]),
                Proposal.val_uuid.in_(artifact_ids),
            )
        )
        for orphan in orphan_result.scalars().all():
            orphan.proposal_status = ProposalStatus.CANCELED.value
            logger.info("Auto-canceled %s proposal %s (container %s committed upstream)",
                        orphan.proposal_type, orphan.id, container.id)

        # Cancel CreateArtifact + CommitArtifact proposals targeting this container
        container_result = await self.db.execute(
            select(Proposal).where(
                Proposal.community_id == container.community_id,
                Proposal.proposal_status.in_(active_statuses),
                Proposal.proposal_type.in_([
                    ProposalType.CREATE_ARTIFACT.value,
                    ProposalType.COMMIT_ARTIFACT.value,
                ]),
                Proposal.val_uuid == container.id,
            )
        )
        for orphan in container_result.scalars().all():
            orphan.proposal_status = ProposalStatus.CANCELED.value
            logger.info("Auto-canceled %s proposal %s (container %s committed)",
                        orphan.proposal_type, orphan.id, container.id)

        # 3. Cascade to sub-containers (artifacts delegated further downstream)
        sub_result = await self.db.execute(
            select(ArtifactContainer).where(
                ArtifactContainer.delegated_from_artifact_id.in_(artifact_ids),
                ArtifactContainer.status != ContainerStatus.COMMITTED,
            )
        )
        for sub_container in sub_result.scalars().all():
            logger.info("Cascade-committing sub-container %s (parent artifact committed)",
                        sub_container.id)
            sub_container.status = ContainerStatus.COMMITTED
            sub_container.committed_at = datetime.now(timezone.utc)
            # Recursively cleanup the sub-container
            await self._cleanup_committed_container(sub_container)

    async def on_parent_proposal_rejected(self, parent_proposal_id: uuid.UUID) -> None:
        """Called by event subscriber when a 'proposal.rejected' event fires.

        If the rejected proposal is the auto-generated EditArtifact for some
        sub-action's pending container, flip that container back to OPEN so
        the sub-action can edit and re-commit.
        """
        result = await self.db.execute(
            select(ArtifactContainer).where(
                ArtifactContainer.pending_parent_proposal_id == parent_proposal_id
            )
        )
        container = result.scalar_one_or_none()
        if not container:
            return
        container.status = ContainerStatus.OPEN
        container.pending_parent_proposal_id = None
        await self.db.flush()
        logger.info(
            "ArtifactService: container %s reopened (parent proposal %s rejected)",
            container.id,
            parent_proposal_id,
        )


def parse_ordered_uuid_list(raw: str) -> list[uuid.UUID]:
    """Parse the JSON UUID list payload from CommitArtifact.val_text."""
    if not raw:
        raise ArtifactServiceError("CommitArtifact.val_text must contain a JSON list of artifact ids")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ArtifactServiceError(f"CommitArtifact.val_text is not valid JSON: {e}")
    if not isinstance(parsed, list):
        raise ArtifactServiceError("CommitArtifact.val_text must be a JSON list")
    out: list[uuid.UUID] = []
    for item in parsed:
        try:
            out.append(uuid.UUID(str(item)))
        except (ValueError, TypeError):
            raise ArtifactServiceError(f"Invalid UUID in CommitArtifact.val_text: {item}")
    return out
