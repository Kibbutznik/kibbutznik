"""Deliberation-tree service for the Reason model."""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.models.proposal import Proposal
from kbz.models.reason import (
    STANCE_CON, STANCE_PRO, STATUS_ACTIVE, Reason,
)
from kbz.schemas.reason import ReasonCreate
from kbz.services.member_service import MemberService


def _opposite(stance: str) -> str:
    return STANCE_CON if stance == STANCE_PRO else STANCE_PRO


class ReasonService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self, proposal_id: uuid.UUID, data: ReasonCreate,
    ) -> Reason:
        # Resolve the proposal to (a) confirm it exists and (b) read
        # its community_id so we can enforce the membership gate.
        proposal = (
            await self.db.execute(
                select(Proposal).where(Proposal.id == proposal_id)
            )
        ).scalar_one_or_none()
        if proposal is None:
            raise HTTPException(status_code=404, detail="Proposal not found")

        # Only members of the proposal's community can argue under
        # it. Non-members already can't propose; they shouldn't be
        # able to seed the deliberation tree either.
        if not await MemberService(self.db).is_active_member(
            proposal.community_id, data.user_id,
        ):
            raise HTTPException(
                status_code=403,
                detail="Only active members of the proposal's community may argue here",
            )

        # If this is a counter-reply, validate the parent and force
        # opposite stance — otherwise the tree devolves into pure
        # agreement chains, which defeats the point of the
        # structure.
        if data.parent_reason_id is not None:
            parent = (
                await self.db.execute(
                    select(Reason).where(Reason.id == data.parent_reason_id)
                )
            ).scalar_one_or_none()
            if parent is None or parent.proposal_id != proposal_id:
                raise HTTPException(
                    status_code=400,
                    detail="parent_reason_id must reference a reason on this same proposal",
                )
            if parent.status != STATUS_ACTIVE:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot reply to a removed parent reason",
                )
            if data.stance == parent.stance:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "A counter-reply must take the OPPOSITE stance from "
                        f"its parent (parent is {parent.stance!r}, this would "
                        f"be {data.stance!r}). Top-level reasons can be "
                        "either pro or con."
                    ),
                )

        reason = Reason(
            id=uuid.uuid4(),
            proposal_id=proposal_id,
            user_id=data.user_id,
            stance=data.stance,
            claim_text=data.claim_text,
            parent_reason_id=data.parent_reason_id,
            status=STATUS_ACTIVE,
        )
        self.db.add(reason)
        await self.db.commit()
        await self.db.refresh(reason)
        return reason

    async def list_for_proposal(
        self, proposal_id: uuid.UUID,
    ) -> list[Reason]:
        """Flat list of all ACTIVE reasons under the proposal,
        oldest-first within each stance. Clients reconstruct the
        tree via parent_reason_id; the read API stays simple."""
        rows = await self.db.execute(
            select(Reason)
            .where(
                Reason.proposal_id == proposal_id,
                Reason.status == STATUS_ACTIVE,
            )
            .order_by(Reason.stance.asc(), Reason.created_at.asc())
        )
        return list(rows.scalars().all())
