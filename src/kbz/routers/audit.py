"""Governance decision provenance.

GET /communities/{id}/audit
   → Chronological list of decided proposals (Accepted / Rejected /
     Canceled), each enriched with: who supported, who proposed,
     when it landed, type, text snippet.

This is the human-readable answer to "show me what's been decided
here and how". Useful for new members browsing a community's
history, for governance audits, and for the dashboard's
"recent rulings" surface. Distinct from /communities/{id}/proposals
which mixes in still-in-flight rows and lacks the supporters
join.

Read-only — write paths (proposal creation, support, pulse
execution) already feed the underlying tables.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.enums import ProposalStatus
from kbz.models.bot_profile import BotProfile
from kbz.models.community import Community
from kbz.models.proposal import Proposal
from kbz.models.support import Support
from kbz.models.user import User
from kbz.services.community_service import CommunityService

router = APIRouter()


class AuditSupporter(BaseModel):
    user_id: uuid.UUID
    user_name: str | None
    display_name: str | None  # bot display name if any


class AuditEntry(BaseModel):
    proposal_id: uuid.UUID
    proposal_type: str
    proposal_status: Literal["Accepted", "Rejected", "Canceled"]
    proposal_text: str
    pitch: str | None
    val_text: str | None
    val_uuid: uuid.UUID | None
    author_user_id: uuid.UUID
    author_user_name: str | None
    author_display_name: str | None
    decided_at: datetime | None
    created_at: datetime
    support_count_at_decide: int
    supporters: list[AuditSupporter]


_TERMINAL_STATUSES = (
    ProposalStatus.ACCEPTED,
    ProposalStatus.REJECTED,
    ProposalStatus.CANCELED,
)


@router.get(
    "/communities/{community_id}/audit",
    response_model=list[AuditEntry],
)
async def community_audit(
    community_id: uuid.UUID,
    statuses: list[Literal["Accepted", "Rejected", "Canceled"]] | None = Query(
        default=None,
        description=(
            "Filter to specific terminal statuses. Default = all three. "
            "Pass repeatedly: ?statuses=Accepted&statuses=Rejected"
        ),
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[AuditEntry]:
    # Confirm the community exists so a typo'd id doesn't return an
    # ambiguously-empty audit.
    if await CommunityService(db).get(community_id) is None:
        raise HTTPException(status_code=404, detail="Community not found")

    if not statuses:
        wanted = list(_TERMINAL_STATUSES)
    else:
        wanted = [ProposalStatus(s) for s in statuses]

    proposals = (
        await db.execute(
            select(Proposal)
            .where(
                Proposal.community_id == community_id,
                Proposal.proposal_status.in_(wanted),
            )
            .order_by(
                # Decided rows first by decision time. Legacy rows
                # without a decided_at fall back to created_at so
                # they still appear in chronological order.
                Proposal.decided_at.desc().nullslast(),
                Proposal.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    if not proposals:
        return []

    proposal_ids = [p.id for p in proposals]
    author_ids = {p.user_id for p in proposals}

    # Single batch fetch of supporters across all returned proposals.
    support_rows = (
        await db.execute(
            select(Support.proposal_id, Support.user_id)
            .where(Support.proposal_id.in_(proposal_ids))
        )
    ).all()
    supporter_ids_by_prop: dict[uuid.UUID, list[uuid.UUID]] = {}
    for pid, uid in support_rows:
        supporter_ids_by_prop.setdefault(pid, []).append(uid)
        author_ids.add(uid)

    # Batch fetch user names.
    user_rows = (
        await db.execute(
            select(User.id, User.user_name).where(User.id.in_(author_ids))
        )
    ).all()
    user_name_by_id = {uid: name for uid, name in user_rows}

    # Batch fetch bot display names scoped to this community.
    bot_rows = (
        await db.execute(
            select(BotProfile.user_id, BotProfile.display_name).where(
                BotProfile.community_id == community_id,
                BotProfile.user_id.in_(author_ids),
            )
        )
    ).all()
    bot_display_by_id = {uid: name for uid, name in bot_rows}

    out: list[AuditEntry] = []
    for p in proposals:
        sup_ids = supporter_ids_by_prop.get(p.id, [])
        supporters = [
            AuditSupporter(
                user_id=uid,
                user_name=user_name_by_id.get(uid),
                display_name=bot_display_by_id.get(uid),
            )
            for uid in sup_ids
        ]
        out.append(AuditEntry(
            proposal_id=p.id,
            proposal_type=str(p.proposal_type),
            proposal_status=str(p.proposal_status),
            proposal_text=p.proposal_text or "",
            pitch=p.pitch,
            val_text=p.val_text,
            val_uuid=p.val_uuid,
            author_user_id=p.user_id,
            author_user_name=user_name_by_id.get(p.user_id),
            author_display_name=bot_display_by_id.get(p.user_id),
            decided_at=getattr(p, "decided_at", None),
            created_at=p.created_at,
            support_count_at_decide=p.support_count,
            supporters=supporters,
        ))
    return out
