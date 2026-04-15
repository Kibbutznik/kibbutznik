import math
import uuid

from fastapi import HTTPException
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import MemberStatus, PulseStatus, ProposalStatus
from kbz.models.member import Member
from kbz.models.proposal import Proposal
from kbz.models.pulse import Pulse
from kbz.models.support import Support, PulseSupport
from kbz.services.event_bus import event_bus
from kbz.services.member_service import MemberService
from kbz.services.pulse_service import PulseService


class SupportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def add_proposal_support(self, proposal_id: uuid.UUID, user_id: uuid.UUID) -> None:
        # Check proposal exists and is in supportable state
        result = await self.db.execute(select(Proposal).where(Proposal.id == proposal_id))
        proposal = result.scalar_one_or_none()
        if not proposal:
            raise HTTPException(status_code=404, detail="Proposal not found")
        if proposal.proposal_status not in (ProposalStatus.OUT_THERE, ProposalStatus.ON_THE_AIR):
            raise HTTPException(status_code=400, detail="Proposal is not in a supportable state")

        # Check user is active member
        member_svc = MemberService(self.db)
        if not await member_svc.is_active_member(proposal.community_id, user_id):
            raise HTTPException(status_code=403, detail="User is not an active member")

        # Check no duplicate support
        existing = await self.db.execute(
            select(Support).where(Support.user_id == user_id, Support.proposal_id == proposal_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Already supporting this proposal")

        # Add support
        support = Support(user_id=user_id, proposal_id=proposal_id, support_value=1)
        self.db.add(support)

        # Increment counter
        await self.db.execute(
            update(Proposal)
            .where(Proposal.id == proposal_id)
            .values(support_count=Proposal.support_count + 1)
        )
        await self.db.commit()
        # Emit so the TKG ingestor can open SUPPORTED + ALLIED_WITH edges.
        await event_bus.emit(
            "support.cast",
            community_id=proposal.community_id,
            user_id=user_id,
            proposal_id=proposal_id,
            author_id=proposal.user_id,
        )

    async def remove_proposal_support(self, proposal_id: uuid.UUID, user_id: uuid.UUID) -> None:
        result = await self.db.execute(
            select(Support).where(Support.user_id == user_id, Support.proposal_id == proposal_id)
        )
        existing = result.scalar_one_or_none()
        if not existing:
            raise HTTPException(status_code=404, detail="Support not found")

        await self.db.execute(
            delete(Support).where(Support.user_id == user_id, Support.proposal_id == proposal_id)
        )
        await self.db.execute(
            update(Proposal)
            .where(Proposal.id == proposal_id)
            .values(support_count=Proposal.support_count - 1)
        )
        # Fetch community_id for the event (we already deleted support, but
        # the proposal row still exists).
        prop = (
            await self.db.execute(
                select(Proposal.community_id).where(Proposal.id == proposal_id)
            )
        ).scalar_one_or_none()
        await self.db.commit()
        await event_bus.emit(
            "support.withdrawn",
            community_id=prop,
            user_id=user_id,
            proposal_id=proposal_id,
        )

    async def add_pulse_support(self, community_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        # Check user is active member
        member_svc = MemberService(self.db)
        if not await member_svc.is_active_member(community_id, user_id):
            raise HTTPException(status_code=403, detail="User is not an active member")

        # Get next pulse
        result = await self.db.execute(
            select(Pulse).where(
                Pulse.community_id == community_id,
                Pulse.status == PulseStatus.NEXT,
            )
        )
        pulse = result.scalar_one_or_none()
        if not pulse:
            raise HTTPException(status_code=404, detail="No next pulse found")

        # Check no duplicate
        existing = await self.db.execute(
            select(PulseSupport).where(
                PulseSupport.user_id == user_id,
                PulseSupport.pulse_id == pulse.id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Already supporting this pulse")

        # Add support
        ps = PulseSupport(user_id=user_id, pulse_id=pulse.id, community_id=community_id)
        self.db.add(ps)

        # Increment counter
        await self.db.execute(
            update(Pulse)
            .where(Pulse.id == pulse.id)
            .values(support_count=Pulse.support_count + 1)
        )
        # Recalculate threshold from current membership (may have changed since
        # the pulse was created — e.g. members left or were thrown out).
        member_result = await self.db.execute(
            select(Member).where(
                Member.community_id == community_id,
                Member.status == MemberStatus.ACTIVE,
            )
        )
        current_members = len(member_result.scalars().all())
        if current_members > 0:
            from kbz.services.community_service import CommunityService
            csvc = CommunityService(self.db)
            pct_str = await csvc.get_variable_value(community_id, "PulseSupport")
            pulse_support_pct = int(float(pct_str)) if pct_str else 50
            correct_threshold = max(1, math.ceil(current_members * pulse_support_pct / 100))
            if pulse.threshold != correct_threshold:
                pulse.threshold = correct_threshold

        await self.db.commit()

        # Refresh and check threshold
        await self.db.refresh(pulse)
        if pulse.support_count >= pulse.threshold:
            pulse_svc = PulseService(self.db)
            await pulse_svc.execute_pulse(community_id)
            return {"status": "supported", "pulse_triggered": True}

        return {"status": "supported", "pulse_triggered": False}

    async def get_proposal_supporters(self, proposal_id: uuid.UUID) -> list[dict]:
        """Return list of {user_id, created_at} for all supporters of a proposal."""
        result = await self.db.execute(
            select(Support).where(Support.proposal_id == proposal_id)
        )
        supports = result.scalars().all()
        return [
            {"user_id": str(s.user_id), "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in supports
        ]

    async def get_pulse_supporters(self, pulse_id: uuid.UUID) -> list[dict]:
        """Return list of {user_id, created_at} for all supporters of a pulse."""
        result = await self.db.execute(
            select(PulseSupport).where(PulseSupport.pulse_id == pulse_id)
        )
        supports = result.scalars().all()
        return [
            {"user_id": str(s.user_id), "created_at": s.created_at.isoformat() if s.created_at else None}
            for s in supports
        ]

    async def remove_pulse_support(self, community_id: uuid.UUID, user_id: uuid.UUID) -> None:
        result = await self.db.execute(
            select(Pulse).where(
                Pulse.community_id == community_id,
                Pulse.status == PulseStatus.NEXT,
            )
        )
        pulse = result.scalar_one_or_none()
        if not pulse:
            raise HTTPException(status_code=404, detail="No next pulse found")

        await self.db.execute(
            delete(PulseSupport).where(
                PulseSupport.user_id == user_id,
                PulseSupport.pulse_id == pulse.id,
            )
        )
        await self.db.execute(
            update(Pulse)
            .where(Pulse.id == pulse.id)
            .values(support_count=Pulse.support_count - 1)
        )
        await self.db.commit()
