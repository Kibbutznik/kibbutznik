import uuid

from fastapi import HTTPException
from sqlalchemy import func, select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.enums import ProposalStatus, ProposalType
from kbz.models.proposal import Proposal
from kbz.models.support import Support
from kbz.schemas.proposal import ProposalCreate
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

        proposal = Proposal(
            id=uuid.uuid4(),
            community_id=community_id,
            user_id=data.user_id,
            proposal_type=data.proposal_type,
            proposal_status=ProposalStatus.DRAFT,
            proposal_text=data.proposal_text,
            val_uuid=data.val_uuid,
            val_text=data.val_text,
            age=0,
            support_count=0,
        )
        self.db.add(proposal)
        await self.db.commit()
        await self.db.refresh(proposal)
        return proposal

    async def get(self, proposal_id: uuid.UUID) -> Proposal | None:
        result = await self.db.execute(select(Proposal).where(Proposal.id == proposal_id))
        return result.scalar_one_or_none()

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

        # Clear ALL supports — the proposal changed, supporters must re-evaluate
        await self.db.execute(
            delete(Support).where(Support.proposal_id == proposal_id)
        )
        proposal.support_count = 0

        await self.db.commit()
        await self.db.refresh(proposal)
        return proposal
