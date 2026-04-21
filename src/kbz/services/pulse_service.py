import math
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import (
    ProposalStatus,
    PulseStatus,
    PROPOSAL_TYPE_THRESHOLDS,
    ProposalType,
)
from kbz.models.community import Community
from kbz.models.proposal import Proposal
from kbz.models.pulse import Pulse
from kbz.services.closeness_service import ClosenessService
from kbz.services.event_bus import event_bus
from kbz.services.execution_service import ExecutionService
from kbz.services.member_service import MemberService


class PulseService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, pulse_id: uuid.UUID) -> Pulse | None:
        result = await self.db.execute(select(Pulse).where(Pulse.id == pulse_id))
        return result.scalar_one_or_none()

    async def list_by_community(self, community_id: uuid.UUID) -> list[Pulse]:
        result = await self.db.execute(
            select(Pulse)
            .where(Pulse.community_id == community_id)
            .order_by(Pulse.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_next_pulse(self, community_id: uuid.UUID) -> Pulse | None:
        result = await self.db.execute(
            select(Pulse).where(
                Pulse.community_id == community_id,
                Pulse.status == PulseStatus.NEXT,
            )
        )
        return result.scalar_one_or_none()

    async def get_active_pulse(self, community_id: uuid.UUID) -> Pulse | None:
        result = await self.db.execute(
            select(Pulse).where(
                Pulse.community_id == community_id,
                Pulse.status == PulseStatus.ACTIVE,
            )
        )
        return result.scalar_one_or_none()

    async def _get_variable_value(self, community_id: uuid.UUID, name: str) -> str:
        from kbz.services.community_service import CommunityService
        svc = CommunityService(self.db)
        val = await svc.get_variable_value(community_id, name)
        return val or "0"

    async def execute_pulse(self, community_id: uuid.UUID) -> None:
        """The heart of the governance system. Executes a full pulse cycle in one transaction."""

        # Get community
        result = await self.db.execute(select(Community).where(Community.id == community_id))
        community = result.scalar_one_or_none()
        if not community:
            return

        member_count = community.member_count
        execution_svc = ExecutionService(self.db)
        member_svc = MemberService(self.db)
        closeness_svc = ClosenessService(self.db)

        # --- Step 1: Process Active pulse proposals (accept/reject) ---
        active_pulse = await self.get_active_pulse(community_id)
        if active_pulse:
            on_air_proposals = await self._get_proposals_by_status(
                community_id, ProposalStatus.ON_THE_AIR
            )
            for proposal in on_air_proposals:
                threshold_var = PROPOSAL_TYPE_THRESHOLDS.get(
                    ProposalType(proposal.proposal_type)
                )
                threshold_pct = int(float(await self._get_variable_value(community_id, threshold_var)))
                threshold = math.ceil(member_count * threshold_pct / 100)

                if proposal.support_count >= threshold:
                    proposal.proposal_status = ProposalStatus.ACCEPTED
                    await execution_svc.execute_proposal(proposal)
                else:
                    proposal.proposal_status = ProposalStatus.REJECTED
                    # If this rejected Membership proposal had an
                    # escrow, return the fee to the applicant. No-op
                    # when no escrow exists (non-financial community
                    # or fee=0).
                    if proposal.proposal_type == ProposalType.MEMBERSHIP:
                        from kbz.services.wallet_service import WalletService
                        await WalletService(self.db).escrow_refund(proposal.id)
                await closeness_svc.apply_proposal_outcome(community_id, proposal.id)
                await self.db.flush()

            # Mark active pulse as Done
            active_pulse.status = PulseStatus.DONE
            await self.db.flush()

        # --- Step 2: Promote Next pulse to Active ---
        next_pulse = await self.get_next_pulse(community_id)
        if not next_pulse:
            return
        next_pulse.status = PulseStatus.ACTIVE
        await self.db.flush()

        # --- Step 3: Move qualified OutThere proposals to the new Active pulse ---
        max_age = int(float(await self._get_variable_value(community_id, "MaxAge")))
        proposal_support_pct = int(float(await self._get_variable_value(community_id, "ProposalSupport")))

        out_there_proposals = await self._get_proposals_by_status(
            community_id, ProposalStatus.OUT_THERE
        )
        for proposal in out_there_proposals:
            # Increment age
            proposal.age += 1

            # Cancel if too old
            if proposal.age > max_age:
                proposal.proposal_status = ProposalStatus.CANCELED
                # Refund Membership escrow if one exists
                if proposal.proposal_type == ProposalType.MEMBERSHIP:
                    from kbz.services.wallet_service import WalletService
                    await WalletService(self.db).escrow_refund(proposal.id)
                await self.db.flush()
                continue

            # Check if enough support to move to OnTheAir
            required_support = math.ceil(member_count * proposal_support_pct / 100)
            if proposal.support_count >= required_support:
                proposal.proposal_status = ProposalStatus.ON_THE_AIR
                proposal.pulse_id = next_pulse.id
            await self.db.flush()

        # --- Step 4: Increment seniority for all active members ---
        await member_svc.increment_seniority(community_id)

        # --- Step 5: Create new Next pulse ---
        # Refresh community for updated member count
        await self.db.refresh(community)
        pulse_support_pct = int(float(await self._get_variable_value(community_id, "PulseSupport")))
        new_threshold = max(1, math.ceil(community.member_count * pulse_support_pct / 100))

        new_pulse = Pulse(
            id=uuid.uuid4(),
            community_id=community_id,
            status=PulseStatus.NEXT,
            support_count=0,
            threshold=new_threshold,
        )
        self.db.add(new_pulse)

        await self.db.commit()

        # Emit events
        await event_bus.emit("pulse.executed", community_id=community_id, pulse_id=next_pulse.id)
        if active_pulse:
            for p in on_air_proposals:
                await event_bus.emit(
                    f"proposal.{p.proposal_status.lower()}",
                    community_id=community_id,
                    user_id=p.user_id,
                    proposal_id=p.id,
                    proposal_type=p.proposal_type,
                )

    async def _get_proposals_by_status(
        self, community_id: uuid.UUID, status: ProposalStatus
    ) -> list[Proposal]:
        result = await self.db.execute(
            select(Proposal).where(
                Proposal.community_id == community_id,
                Proposal.proposal_status == status,
            )
        )
        return list(result.scalars().all())
