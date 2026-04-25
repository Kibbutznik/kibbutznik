import math
import uuid
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import DEFAULT_VARIABLES, PROPOSAL_TYPE_THRESHOLDS, ProposalStatus, ProposalType
from kbz.models.bot_profile import BotProfile
from kbz.models.community import Community
from kbz.models.proposal import Proposal
from kbz.models.support import Support
from kbz.models.user import User
from kbz.models.variable import Variable
from kbz.schemas.proposal import ProposalCreate
from kbz.services.event_bus import event_bus
from kbz.services.member_service import MemberService


# ── Duplicate-proposal rules ─────────────────────────────────────────
# Each rule says: when a proposal of this type is being created, look
# for an existing in-flight proposal in the same community matching on
# the listed fields. If found, reject the create.
#
# Field tokens:
#   "val_uuid"      → Proposal.val_uuid == data.val_uuid
#   "val_text"      → Proposal.val_text == data.val_text
#   "proposal_text" → Proposal.proposal_text == data.proposal_text
#   "user_id"       → Proposal.user_id    == data.user_id (per-proposer)
#   "applicant"     → coalesce(val_uuid,user_id) match — for Membership
#                     where the applicant may be self (user_id) or
#                     someone else (val_uuid).
#
# Types intentionally NOT in this table (FUNDING/PAYMENT/PAY_BACK/
# DIVIDEND) allow legitimate parallel proposals with different amounts.
DEDUPE_RULES: dict[ProposalType, tuple[str, ...]] = {
    ProposalType.MEMBERSHIP:           ("applicant",),
    ProposalType.THROW_OUT:            ("val_uuid",),
    ProposalType.ADD_STATEMENT:        ("proposal_text",),
    ProposalType.REMOVE_STATEMENT:     ("val_uuid",),
    ProposalType.REPLACE_STATEMENT:    ("val_uuid", "val_text"),
    ProposalType.CHANGE_VARIABLE:      ("proposal_text",),
    ProposalType.ADD_ACTION:           ("val_text",),
    ProposalType.END_ACTION:           ("val_uuid",),
    ProposalType.JOIN_ACTION:          ("val_uuid", "user_id"),
    ProposalType.SET_MEMBERSHIP_HANDLER: ("val_uuid",),
    ProposalType.CREATE_ARTIFACT:      ("val_uuid", "val_text"),
    ProposalType.EDIT_ARTIFACT:        ("val_uuid", "user_id"),
    ProposalType.REMOVE_ARTIFACT:      ("val_uuid",),
    ProposalType.DELEGATE_ARTIFACT:    ("val_uuid", "val_text"),
    ProposalType.COMMIT_ARTIFACT:      ("val_uuid",),
}

_ACTIVE_DEDUPE_STATUSES = (
    ProposalStatus.DRAFT,
    ProposalStatus.OUT_THERE,
    ProposalStatus.ON_THE_AIR,
)


class ProposalService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, community_id: uuid.UUID, data: ProposalCreate) -> Proposal:
        # Validate proposal type
        try:
            ProposalType(data.proposal_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid proposal type: {data.proposal_type}")

        # Validate user is active member (except for Membership proposals)
        if data.proposal_type != ProposalType.MEMBERSHIP:
            member_svc = MemberService(self.db)
            if not await member_svc.is_active_member(community_id, data.user_id):
                raise HTTPException(status_code=403, detail="User is not an active member")

        # Block duplicate proposals (see DEDUPE_RULES for the per-type matrix).
        try:
            ptype_enum = ProposalType(data.proposal_type)
        except ValueError:
            ptype_enum = None
        if ptype_enum is not None and ptype_enum in DEDUPE_RULES:
            fields = DEDUPE_RULES[ptype_enum]
            dup_q = select(Proposal).where(
                Proposal.community_id == community_id,
                Proposal.proposal_type == ptype_enum,
                Proposal.proposal_status.in_(_ACTIVE_DEDUPE_STATUSES),
            )
            ok = True
            for f in fields:
                if f == "val_uuid":
                    if data.val_uuid is None:
                        ok = False; break
                    dup_q = dup_q.where(Proposal.val_uuid == data.val_uuid)
                elif f == "val_text":
                    if not data.val_text:
                        ok = False; break
                    dup_q = dup_q.where(Proposal.val_text == data.val_text)
                elif f == "proposal_text":
                    if not data.proposal_text:
                        ok = False; break
                    # CHANGE_VARIABLE encodes "varName\nreason..." in proposal_text;
                    # dedupe on the first line only (the variable name).
                    if ptype_enum == ProposalType.CHANGE_VARIABLE:
                        var_name = data.proposal_text.split("\n", 1)[0].strip()
                        if not var_name:
                            ok = False; break
                        dup_q = dup_q.where(
                            func.split_part(Proposal.proposal_text, "\n", 1) == var_name
                        )
                    else:
                        dup_q = dup_q.where(Proposal.proposal_text == data.proposal_text)
                elif f == "user_id":
                    dup_q = dup_q.where(Proposal.user_id == data.user_id)
                elif f == "applicant":
                    target = data.val_uuid or data.user_id
                    dup_q = dup_q.where(
                        func.coalesce(Proposal.val_uuid, Proposal.user_id) == target
                    )
            if ok:
                existing = (await self.db.execute(dup_q.limit(1))).scalars().first()
                if existing:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Duplicate {data.proposal_type}: an in-flight proposal "
                            f"({existing.id}, status {existing.proposal_status}) already "
                            f"covers the same target. Support that one instead of creating a duplicate."
                        ),
                    )

        # For EditArtifact, snapshot the artifact's CURRENT content so the
        # diff (what was replaced) survives even after the artifact moves on.
        prev_content = None
        if ptype_enum == ProposalType.EDIT_ARTIFACT and data.val_uuid is not None:
            from kbz.models.artifact import Artifact
            art = (
                await self.db.execute(
                    select(Artifact).where(Artifact.id == data.val_uuid)
                )
            ).scalar_one_or_none()
            if art is not None:
                prev_content = art.content

        proposal = Proposal(
            id=uuid.uuid4(),
            community_id=community_id,
            user_id=data.user_id,
            proposal_type=data.proposal_type,
            proposal_status=ProposalStatus.DRAFT,
            proposal_text=data.proposal_text,
            pitch=(data.pitch or None),
            val_uuid=data.val_uuid,
            val_text=data.val_text,
            prev_content=prev_content,
            age=0,
            support_count=0,
        )
        self.db.add(proposal)
        await self.db.flush()

        # Membership escrow: if the target community has the Financial
        # module on AND a positive `membershipFee` variable, debit the
        # fee from the applicant's user wallet into an escrow tied to
        # this proposal. On accept the Membership handler releases;
        # on reject/cancel ProposalService.set_status refunds.
        if data.proposal_type == ProposalType.MEMBERSHIP:
            from decimal import Decimal, InvalidOperation
            from kbz.services.wallet_service import (
                WalletService, InsufficientFundsError, OWNER_USER,
            )
            wallet_svc = WalletService(self.db)
            if await wallet_svc.is_financial(community_id):
                fee_row = (
                    await self.db.execute(
                        select(Variable.value).where(
                            Variable.community_id == community_id,
                            Variable.name == "membershipFee",
                        )
                    )
                ).scalar_one_or_none()
                try:
                    fee = Decimal(fee_row or "0")
                except (InvalidOperation, TypeError):
                    fee = Decimal("0")
                if fee > 0:
                    applicant_wallet = await wallet_svc.get_or_create(
                        OWNER_USER, data.user_id, gate=False,
                    )
                    try:
                        await wallet_svc.escrow_open(
                            proposal.id, fee, applicant_wallet,
                            memo=f"Membership escrow for {proposal.id}",
                        )
                    except (InsufficientFundsError, ValueError) as e:
                        # Roll back the proposal creation so the
                        # applicant doesn't leave a ghost row behind.
                        await self.db.rollback()
                        raise HTTPException(
                            status_code=402,
                            detail=f"Insufficient credits for {fee} membership fee: {e}",
                        )

        # Fan out a Notification row to every other active member
        # BEFORE we commit, so the proposal + its inbox entries land
        # atomically. A reader who sees the proposal in /communities
        # is guaranteed to see its notification too.
        from kbz.services.notification_service import NotificationService
        notif_svc = NotificationService(self.db)
        await notif_svc.fanout_proposal_created(
            community_id=community_id,
            proposal_id=proposal.id,
            proposal_type=str(proposal.proposal_type),
            proposal_text=proposal.proposal_text or proposal.val_text or "",
            author_user_id=proposal.user_id,
        )
        # Targeted-user heads-up: ThrowOut (val_uuid = victim) and
        # Membership-by-someone-else (val_uuid = applicant, but
        # author isn't them) deserve a per-target row in addition to
        # the broadcast. This is the "thrown out while on vacation"
        # scenario — never let the vote happen without at least
        # pinging the person.
        if (
            data.proposal_type in (ProposalType.THROW_OUT, ProposalType.MEMBERSHIP)
            and data.val_uuid is not None
        ):
            await notif_svc.fanout_proposal_targets_you(
                community_id=community_id,
                proposal_id=proposal.id,
                proposal_type=str(proposal.proposal_type),
                proposal_text=proposal.proposal_text or proposal.val_text or "",
                target_user_id=data.val_uuid,
                author_user_id=proposal.user_id,
            )

        await self.db.commit()
        await self.db.refresh(proposal)
        # Emit so the TKG ingestor can record AUTHORED and embed the text.
        await event_bus.emit(
            "proposal.created",
            community_id=community_id,
            user_id=proposal.user_id,
            proposal_id=proposal.id,
            proposal_type=str(proposal.proposal_type),
            proposal_text=proposal.proposal_text or proposal.val_text or "",
        )
        return proposal

    async def get(self, proposal_id: uuid.UUID) -> Proposal | None:
        result = await self.db.execute(select(Proposal).where(Proposal.id == proposal_id))
        return result.scalar_one_or_none()

    async def enrich(
        self, proposals: list[Proposal], community_id: uuid.UUID
    ) -> list[SimpleNamespace]:
        """Attach computed thresholds + author metadata for API responses.

        `promote_threshold` is the support count needed to move OutThere →
        OnTheAir (ProposalSupport %). `decide_threshold` is the per-type
        threshold for execution when OnTheAir. Both use the CURRENT member
        count — stale thresholds on the proposal row itself are ignored."""
        if not proposals:
            return []

        from kbz.services.community_service import CommunityService
        csvc = CommunityService(self.db)
        variables = await csvc.get_variables(community_id)
        member_count = await csvc.get_member_count(community_id) or 1

        def pct_threshold(var_name: str) -> int:
            raw = variables.get(var_name) or DEFAULT_VARIABLES.get(var_name, "0")
            try:
                pct = int(float(raw))
            except (TypeError, ValueError):
                pct = 0
            return max(1, math.ceil(member_count * pct / 100))

        promote_threshold = pct_threshold("ProposalSupport")

        user_ids = list({p.user_id for p in proposals})
        user_by_id: dict[uuid.UUID, str] = {}
        bot_by_id: dict[uuid.UUID, str] = {}
        if user_ids:
            user_rows = (
                await self.db.execute(
                    select(User.id, User.user_name).where(User.id.in_(user_ids))
                )
            ).all()
            user_by_id = {uid: name for uid, name in user_rows}
            bot_rows = (
                await self.db.execute(
                    select(BotProfile.user_id, BotProfile.display_name).where(
                        BotProfile.community_id == community_id,
                        BotProfile.user_id.in_(user_ids),
                    )
                )
            ).all()
            bot_by_id = {uid: name for uid, name in bot_rows}

        enriched: list[SimpleNamespace] = []
        for p in proposals:
            try:
                ptype = ProposalType(p.proposal_type)
                decide_var = PROPOSAL_TYPE_THRESHOLDS.get(ptype)
            except ValueError:
                decide_var = None
            decide_threshold = pct_threshold(decide_var) if decide_var else None
            enriched.append(SimpleNamespace(
                id=p.id,
                community_id=p.community_id,
                user_id=p.user_id,
                proposal_type=p.proposal_type,
                proposal_status=p.proposal_status,
                proposal_text=p.proposal_text,
                pitch=p.pitch,
                val_uuid=p.val_uuid,
                val_text=p.val_text,
                pulse_id=p.pulse_id,
                age=p.age,
                support_count=p.support_count,
                created_at=p.created_at,
                prev_content=p.prev_content,
                promote_threshold=promote_threshold,
                decide_threshold=decide_threshold,
                user_name=user_by_id.get(p.user_id),
                display_name=bot_by_id.get(p.user_id),
            ))
        return enriched

    async def enrich_one(
        self, proposal: Proposal, community_id: uuid.UUID | None = None
    ) -> SimpleNamespace:
        cid = community_id or proposal.community_id
        [out] = await self.enrich([proposal], cid)
        return out

    async def submit(self, proposal_id: uuid.UUID) -> Proposal | None:
        proposal = await self.get(proposal_id)
        if not proposal or proposal.proposal_status != ProposalStatus.DRAFT:
            raise HTTPException(status_code=400, detail="Only draft proposals can be submitted")
        proposal.proposal_status = ProposalStatus.OUT_THERE
        await self.db.commit()
        await self.db.refresh(proposal)
        return proposal

    async def list_by_community(
        self, community_id: uuid.UUID, status: str | None = None, user_id: uuid.UUID | None = None,
        val_uuid: uuid.UUID | None = None, proposal_type: str | None = None,
        pulse_id: uuid.UUID | None = None,
    ) -> list[Proposal]:
        query = select(Proposal).where(Proposal.community_id == community_id)
        if status:
            query = query.where(Proposal.proposal_status == status)
        if user_id:
            query = query.where(Proposal.user_id == user_id)
        if val_uuid:
            query = query.where(Proposal.val_uuid == val_uuid)
        if proposal_type:
            query = query.where(Proposal.proposal_type == proposal_type)
        if pulse_id:
            query = query.where(Proposal.pulse_id == pulse_id)
        result = await self.db.execute(query.order_by(Proposal.created_at.desc()))
        return list(result.scalars().all())

    async def list_by_status(
        self, community_id: uuid.UUID, status: ProposalStatus
    ) -> list[Proposal]:
        result = await self.db.execute(
            select(Proposal).where(
                Proposal.community_id == community_id,
                Proposal.proposal_status == status,
            )
        )
        return list(result.scalars().all())

    async def edit_text(
        self, proposal_id: uuid.UUID, user_id: uuid.UUID,
        new_text: str | None = None, new_val_text: str | None = None,
        new_pitch: str | None = None,
    ) -> Proposal:
        """Edit a proposal's text. Resets ALL support (supporters must re-evaluate)."""
        proposal = await self.get(proposal_id)
        if not proposal:
            raise HTTPException(status_code=404, detail="Proposal not found")
        if proposal.user_id != user_id:
            raise HTTPException(status_code=403, detail="Only the proposal creator can edit it")
        if proposal.proposal_status not in (ProposalStatus.DRAFT, ProposalStatus.OUT_THERE):
            raise HTTPException(
                status_code=400,
                detail="Only Draft or OutThere proposals can be edited",
            )

        # Update the text
        if new_text is not None:
            proposal.proposal_text = new_text
        if new_val_text is not None:
            proposal.val_text = new_val_text
        if new_pitch is not None:
            proposal.pitch = new_pitch or None

        # Clear ALL supports — the proposal changed, supporters must re-evaluate
        await self.db.execute(
            delete(Support).where(Support.proposal_id == proposal_id)
        )
        proposal.support_count = 0

        await self.db.commit()
        await self.db.refresh(proposal)
        return proposal
