import math
import uuid

from fastapi import HTTPException
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import MemberStatus, PulseStatus, ProposalStatus
from kbz.models.bot_profile import BotProfile
from kbz.models.member import Member
from kbz.models.proposal import Proposal
from kbz.models.pulse import Pulse
from kbz.models.support import Support, PulseSupport
from kbz.models.user import User
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

        # Add support + increment counter inside one try/except. The
        # IntegrityError on the (user_id, proposal_id) primary key can
        # fire on either autoflush (the next UPDATE forces the pending
        # Support INSERT) or on the explicit commit. Both paths now
        # land here. Race-window safety net: two concurrent same-user
        # supporters can both pass the existence check above, both
        # `db.add(support)`, and only get caught by the PK on commit.
        # Without this guard the loser sees a 500 instead of a clean
        # 409 — symmetric to the pulse-support race fix.
        try:
            support = Support(user_id=user_id, proposal_id=proposal_id, support_value=1)
            self.db.add(support)

            await self.db.execute(
                update(Proposal)
                .where(Proposal.id == proposal_id)
                .values(support_count=Proposal.support_count + 1)
            )
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise HTTPException(
                status_code=409, detail="Already supporting this proposal",
            )
        # Emit so the TKG ingestor can open SUPPORTED + ALLIED_WITH edges.
        await event_bus.emit(
            "support.cast",
            community_id=proposal.community_id,
            user_id=user_id,
            proposal_id=proposal_id,
            author_id=proposal.user_id,
        )

    async def remove_proposal_support(self, proposal_id: uuid.UUID, user_id: uuid.UUID) -> None:
        # Refuse if the proposal has already landed. Pre-fix a user
        # could DELETE their support AFTER the proposal was
        # Accepted/Rejected/Canceled, which retroactively decremented
        # support_count and corrupted the audit log snapshot
        # (support_count_at_decide reads from the live column). Once
        # the verdict is in, the support row is part of the
        # historical record — withdrawal makes no governance sense.
        proposal = (
            await self.db.execute(
                select(Proposal).where(Proposal.id == proposal_id)
            )
        ).scalar_one_or_none()
        if proposal is None:
            raise HTTPException(status_code=404, detail="Proposal not found")
        if proposal.proposal_status not in (
            ProposalStatus.OUT_THERE, ProposalStatus.ON_THE_AIR,
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot withdraw support — proposal is "
                    f"{proposal.proposal_status}"
                ),
            )

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
        await self.db.commit()
        await event_bus.emit(
            "support.withdrawn",
            community_id=proposal.community_id,
            user_id=user_id,
            proposal_id=proposal_id,
        )

    async def add_pulse_support(self, community_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        # Refuse pulse-support against INACTIVE communities. Same
        # reasoning as ProposalService.create's status gate — once
        # the community is ended, the pulse cycle should stop.
        from kbz.enums import CommunityStatus
        from kbz.models.community import Community
        community = (
            await self.db.execute(
                select(Community).where(Community.id == community_id)
            )
        ).scalar_one_or_none()
        if community is None:
            raise HTTPException(status_code=404, detail="Community not found")
        if community.status != CommunityStatus.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail="Community is not active — cannot support pulses",
            )

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

        # Add support + increment counter + recalc threshold inside
        # one try/except. The IntegrityError can fire on either
        # autoflush (the next SELECT/UPDATE forces the pending
        # PulseSupport INSERT) or on the explicit commit at the
        # end. Both paths land here. Race-window safety net: two
        # concurrent supporters from the SAME user can both pass
        # the existence check above and only get caught by the
        # (user_id, pulse_id) primary key. Without this guard the
        # loser sees a 500 instead of a clean 409.
        try:
            ps = PulseSupport(
                user_id=user_id, pulse_id=pulse.id, community_id=community_id,
            )
            self.db.add(ps)

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
        except IntegrityError:
            await self.db.rollback()
            raise HTTPException(
                status_code=409, detail="Already supporting this pulse",
            )

        # Refresh and check threshold
        await self.db.refresh(pulse)
        if pulse.support_count >= pulse.threshold:
            pulse_svc = PulseService(self.db)
            await pulse_svc.execute_pulse(community_id)
            return {"status": "supported", "pulse_triggered": True}

        return {"status": "supported", "pulse_triggered": False}

    async def get_proposal_supporters(self, proposal_id: uuid.UUID) -> list[dict]:
        """Return list of {user_id, user_name, display_name, created_at} for all supporters."""
        # Join Proposal first — BotProfile's ON clause references
        # Proposal.community_id, so Proposal must already be in the FROM.
        result = await self.db.execute(
            select(Support, User.user_name, BotProfile.display_name)
            .join(Proposal, Proposal.id == Support.proposal_id)
            .outerjoin(User, User.id == Support.user_id)
            .outerjoin(
                BotProfile,
                (BotProfile.user_id == Support.user_id)
                & (BotProfile.community_id == Proposal.community_id),
            )
            .where(Support.proposal_id == proposal_id)
        )
        return [
            {
                "user_id": str(s.user_id),
                "user_name": user_name,
                "display_name": display_name,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s, user_name, display_name in result.all()
        ]

    async def get_pulse_supporters(self, pulse_id: uuid.UUID) -> list[dict]:
        """Return list of {user_id, user_name, display_name, created_at} for all supporters."""
        # Join Pulse first — BotProfile's ON clause references
        # Pulse.community_id, so Pulse must already be in the FROM.
        result = await self.db.execute(
            select(PulseSupport, User.user_name, BotProfile.display_name)
            .join(Pulse, Pulse.id == PulseSupport.pulse_id)
            .outerjoin(User, User.id == PulseSupport.user_id)
            .outerjoin(
                BotProfile,
                (BotProfile.user_id == PulseSupport.user_id)
                & (BotProfile.community_id == Pulse.community_id),
            )
            .where(PulseSupport.pulse_id == pulse_id)
        )
        return [
            {
                "user_id": str(s.user_id),
                "user_name": user_name,
                "display_name": display_name,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s, user_name, display_name in result.all()
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

        # Must check rowcount before decrementing — otherwise a DELETE
        # against a non-existent support row still runs the counter
        # UPDATE and drifts support_count below the true value.
        delete_result = await self.db.execute(
            delete(PulseSupport).where(
                PulseSupport.user_id == user_id,
                PulseSupport.pulse_id == pulse.id,
            )
        )
        if delete_result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Support not found")
        await self.db.execute(
            update(Pulse)
            .where(Pulse.id == pulse.id)
            .values(support_count=Pulse.support_count - 1)
        )
        await self.db.commit()
