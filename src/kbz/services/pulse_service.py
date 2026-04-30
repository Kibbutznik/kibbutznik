import math
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import (
    CommunityStatus,
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

    async def list_by_community(
        self,
        community_id: uuid.UUID,
        *,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[Pulse]:
        # Pre-fix unbounded — old communities have N pulses where N
        # grows with every governance round. Hard cap so a single read
        # can't dump thousands of rows.
        result = await self.db.execute(
            select(Pulse)
            .where(Pulse.community_id == community_id)
            .order_by(Pulse.created_at.desc())
            .limit(limit).offset(offset)
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
        """The heart of the governance system. Executes a full pulse cycle in one transaction.

        Race-window safety: two concurrent threshold-crossing
        pulse-supports both refresh, both see
        ``support_count >= threshold``, and both call this. Without
        protection, both would process the same OnTheAir proposals
        AND both create a new NEXT pulse — leaving the community
        with two NEXT pulses, breaking every subsequent
        ``get_next_pulse()`` with MultipleResultsFound. The partial
        unique indexes on ``(community_id) WHERE status = NEXT/ACTIVE``
        turn the loser's NEW NEXT insert into a clean
        IntegrityError; we rollback and bail so the loser's
        transaction has no observable effect.
        """
        from sqlalchemy.exc import IntegrityError

        try:
            await self._execute_pulse_unsafe(community_id)
        except IntegrityError:
            await self.db.rollback()
            return

    async def _execute_pulse_unsafe(self, community_id: uuid.UUID) -> None:
        # Get community
        result = await self.db.execute(select(Community).where(Community.id == community_id))
        community = result.scalar_one_or_none()
        if not community:
            return
        # INACTIVE communities (closed via accepted EndAction) are
        # frozen — no new pulses cycle, no proposals process. The
        # ProposalService.create + add_pulse_support gates already
        # block new mutations from landing here; this short-circuit
        # ensures any leftover pulse-support row that was committed
        # just before EndAction finalized doesn't trigger a stale
        # cycle.
        if community.status != CommunityStatus.ACTIVE:
            return

        member_count = community.member_count
        execution_svc = ExecutionService(self.db)
        member_svc = MemberService(self.db)
        closeness_svc = ClosenessService(self.db)

        # --- Step 0: Snapshot ALL governance-affecting variables ---
        # Pre-fix the threshold for each OnTheAir proposal was read
        # LIVE inside the for-loop in step 1, AFTER `execute_proposal`
        # for an earlier accepted ChangeVariable could have already
        # mutated the very variable being read. Concrete repro:
        # P1 = ChangeVariable(Membership, "10") at support 4/6,
        # P2 = Membership(Eve) at support 2/6. P1 accepts under OLD
        # 50% threshold and flushes Variable(Membership)=10. The
        # next loop iteration reads 10% as P2's threshold and
        # ACCEPTS Eve at 33% — even though under same-pulse rules
        # P2 should reject. Same shape applied to MaxAge and
        # ProposalSupport between step 1 (OnTheAir verdicts) and
        # step 3 (OutThere aging+promote).
        #
        # Design invariant: a ChangeVariable accepted on this pulse
        # affects the NEXT pulse, not the current one. Snapshot
        # every var that the rest of the pulse reads, BEFORE step 1
        # runs. Step 5's NEW pulse threshold still reads live —
        # that's the legitimate place a same-pulse ChangeVariable
        # lands.
        snapshot_max_age = int(float(await self._get_variable_value(community_id, "MaxAge")))
        snapshot_proposal_support_pct = int(float(await self._get_variable_value(community_id, "ProposalSupport")))
        # Per-OnTheAir-proposal threshold dict, snapshotted before
        # any handler runs.
        active_pulse = await self.get_active_pulse(community_id)
        on_air_proposals: list = []
        snapshot_thresholds: dict = {}
        if active_pulse:
            on_air_proposals = await self._get_proposals_by_status(
                community_id, ProposalStatus.ON_THE_AIR
            )
            # Snapshot threshold per proposal — distinct variable lookups,
            # cached so the loop below uses pre-execution values.
            for proposal in on_air_proposals:
                threshold_var = PROPOSAL_TYPE_THRESHOLDS.get(
                    ProposalType(proposal.proposal_type)
                )
                threshold_pct = int(float(
                    await self._get_variable_value(community_id, threshold_var)
                ))
                # Floor at 1 — see comment in step 1 below.
                snapshot_thresholds[proposal.id] = max(
                    1, math.ceil(member_count * threshold_pct / 100)
                )

            # Defense in depth: ChangeVariable proposals execute LAST
            # within step 1. Even if a future change re-introduces a
            # live read inside the loop, the variable mutations land
            # only after every other proposal has been verdicted.
            on_air_proposals = sorted(
                on_air_proposals,
                key=lambda p: 1 if p.proposal_type == ProposalType.CHANGE_VARIABLE.value else 0,
            )

        # --- Step 1: Process Active pulse proposals (accept/reject) ---
        if active_pulse:
            for proposal in on_air_proposals:
                # Floor at 1 — `ceil(0 * pct / 100) == 0`, and "support_count
                # >= 0" is always true. Without this floor, a community whose
                # member_count somehow hit zero (everyone thrown out, or a
                # mis-counted rollback) would auto-ACCEPT every OnTheAir
                # proposal on the next pulse with no real support behind it.
                # Mirrors the same defense already used at the new-pulse
                # threshold computation below.
                threshold = snapshot_thresholds[proposal.id]

                from datetime import datetime, timezone
                _decided_now = datetime.now(timezone.utc)
                if proposal.support_count >= threshold:
                    proposal.proposal_status = ProposalStatus.ACCEPTED
                    proposal.decided_at = _decided_now
                    await execution_svc.execute_proposal(proposal)
                else:
                    proposal.proposal_status = ProposalStatus.REJECTED
                    proposal.decided_at = _decided_now
                    # If this rejected Membership proposal had an
                    # escrow, return the fee to the applicant. No-op
                    # when no escrow exists (non-financial community
                    # or fee=0).
                    if proposal.proposal_type == ProposalType.MEMBERSHIP:
                        from kbz.services.wallet_service import WalletService
                        await WalletService(self.db).escrow_refund(proposal.id)
                # Inbox: tell the author their proposal landed.
                # Same-transaction so the author can't refresh and
                # see Accepted/Rejected before the notification
                # row exists.
                from kbz.services.notification_service import NotificationService
                from kbz.models.notification import (
                    KIND_PROPOSAL_ACCEPTED, KIND_PROPOSAL_REJECTED,
                )
                outcome_kind = (
                    KIND_PROPOSAL_ACCEPTED
                    if proposal.proposal_status == ProposalStatus.ACCEPTED
                    else KIND_PROPOSAL_REJECTED
                )
                await NotificationService(self.db).fanout_proposal_outcome(
                    community_id=community_id,
                    proposal_id=proposal.id,
                    proposal_type=str(proposal.proposal_type),
                    proposal_text=proposal.proposal_text or "",
                    author_user_id=proposal.user_id,
                    outcome_kind=outcome_kind,
                )
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
        # Use the values snapshotted at step 0, NOT live reads —
        # otherwise a ChangeVariable(MaxAge / ProposalSupport, …)
        # accepted in step 1 would change aging/promotion behavior
        # for THIS pulse, violating the same-pulse design invariant.
        max_age = snapshot_max_age
        proposal_support_pct = snapshot_proposal_support_pct

        out_there_proposals = await self._get_proposals_by_status(
            community_id, ProposalStatus.OUT_THERE
        )
        for proposal in out_there_proposals:
            # Increment age
            proposal.age += 1

            # Cancel if too old
            if proposal.age > max_age:
                from datetime import datetime, timezone
                proposal.proposal_status = ProposalStatus.CANCELED
                proposal.decided_at = datetime.now(timezone.utc)
                # Refund Membership escrow if one exists
                if proposal.proposal_type == ProposalType.MEMBERSHIP:
                    from kbz.services.wallet_service import WalletService
                    await WalletService(self.db).escrow_refund(proposal.id)
                # Inbox: tell the author their proposal aged out.
                # Accepted / Rejected both fire fanout_proposal_outcome
                # on the OnTheAir branch above; without this, the
                # author of an aged-out proposal silently sees it
                # disappear from in-flight with no signal.
                from kbz.services.notification_service import NotificationService
                from kbz.models.notification import KIND_PROPOSAL_CANCELED
                await NotificationService(self.db).fanout_proposal_outcome(
                    community_id=community_id,
                    proposal_id=proposal.id,
                    proposal_type=str(proposal.proposal_type),
                    proposal_text=proposal.proposal_text or "",
                    author_user_id=proposal.user_id,
                    outcome_kind=KIND_PROPOSAL_CANCELED,
                )
                await self.db.flush()
                # Emit so the TKG ingestor stamps canceled_at_round
                # on the proposal node — without this, age-out
                # cancellations were invisible to semantic search
                # and rank-by-status queries.
                await event_bus.emit(
                    "proposal.canceled",
                    community_id=community_id,
                    user_id=proposal.user_id,
                    proposal_id=proposal.id,
                    proposal_type=str(proposal.proposal_type),
                )
                continue

            # Check if enough support to move to OnTheAir.
            # Same floor-at-1 reasoning as the OnTheAir step above:
            # otherwise a 0-member community auto-promotes every
            # OutThere proposal to OnTheAir.
            required_support = max(1, math.ceil(member_count * proposal_support_pct / 100))
            if proposal.support_count >= required_support:
                proposal.proposal_status = ProposalStatus.ON_THE_AIR
                proposal.pulse_id = next_pulse.id
                # Vote-missing reminder for everyone who hasn't yet
                # supported it. The proposal will be decided on the
                # next pulse — this is the last cheap nudge before
                # the verdict lands.
                from kbz.services.notification_service import NotificationService
                await NotificationService(self.db).fanout_proposal_vote_missing(
                    community_id=community_id,
                    proposal_id=proposal.id,
                    proposal_type=str(proposal.proposal_type),
                    proposal_text=proposal.proposal_text or "",
                    author_user_id=proposal.user_id,
                )
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
        # Deterministic ORDER BY (created_at, id) so:
        #  - the order in which step 1 verdicts are applied is
        #    reproducible across snapshot restores and re-tests;
        #  - handlers that side-effect each other (e.g. Membership
        #    admitting a member, Dividend distributing to the active
        #    set) run in a stable order that's easy to reason about;
        #  - the audit log shows pulses in a canonical order even when
        #    Postgres VACUUM has rearranged physical row order.
        # Pre-fix the SELECT had no ORDER BY → DB-undefined order →
        # non-reproducible cross-handler effects.
        result = await self.db.execute(
            select(Proposal).where(
                Proposal.community_id == community_id,
                Proposal.proposal_status == status,
            )
            .order_by(Proposal.created_at.asc(), Proposal.id.asc())
        )
        return list(result.scalars().all())
