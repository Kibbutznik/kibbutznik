"""Self-service endpoints for the logged-in user.

Everything under `/users/me/…` requires a valid session cookie. These
drive the product's Dashboard page: memberships, pending applications,
sent invites. Also `PATCH /users/me` for profile edits.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import require_user
from kbz.database import get_db
from kbz.enums import MemberStatus, ProposalStatus, ProposalType
from kbz.models.auth import Invite
from kbz.models.community import Community
from kbz.models.member import Member
from kbz.models.proposal import Proposal
from kbz.models.user import User

router = APIRouter(prefix="/users/me", tags=["me"])


# ── Schemas ──────────────────────────────────────────────────────────

class MembershipOut(BaseModel):
    community_id: uuid.UUID
    community_name: str
    joined_at: datetime
    seniority: int
    status: int


class PendingApplicationOut(BaseModel):
    proposal_id: uuid.UUID
    community_id: uuid.UUID
    community_name: str
    status: str  # Draft / OutThere / OnTheAir
    support_count: int
    age: int
    created_at: datetime


class SentInviteOut(BaseModel):
    invite_id: uuid.UUID
    invite_code: str
    community_id: uuid.UUID
    community_name: str
    created_at: datetime
    expires_at: datetime
    claimed: bool
    claimed_at: datetime | None


class UpdateMeRequest(BaseModel):
    user_name: str | None = None
    about: str | None = None


# ── Endpoints ────────────────────────────────────────────────────────

@router.get("/memberships", response_model=list[MembershipOut])
async def my_memberships(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Active memberships for the logged-in user across all communities."""
    rows = (
        await db.execute(
            select(Member, Community.name)
            .join(Community, Community.id == Member.community_id)
            .where(Member.user_id == user.id, Member.status == MemberStatus.ACTIVE)
            .order_by(Member.joined_at.desc())
        )
    ).all()
    return [
        MembershipOut(
            community_id=m.community_id,
            community_name=name,
            joined_at=m.joined_at,
            seniority=m.seniority,
            status=int(m.status),
        )
        for m, name in rows
    ]


_PENDING_APP_STATUSES = (
    ProposalStatus.DRAFT,
    ProposalStatus.OUT_THERE,
    ProposalStatus.ON_THE_AIR,
)


@router.get("/pending-applications", response_model=list[PendingApplicationOut])
async def my_pending_applications(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Membership proposals I authored that haven't landed yet.

    Covers all three in-flight statuses so the dashboard can show things
    like "still gathering support" vs "under final vote".
    """
    rows = (
        await db.execute(
            select(Proposal, Community.name)
            .join(Community, Community.id == Proposal.community_id)
            .where(
                Proposal.user_id == user.id,
                Proposal.proposal_type == ProposalType.MEMBERSHIP,
                Proposal.proposal_status.in_(_PENDING_APP_STATUSES),
            )
            .order_by(Proposal.created_at.desc())
        )
    ).all()
    return [
        PendingApplicationOut(
            proposal_id=p.id,
            community_id=p.community_id,
            community_name=name,
            status=str(p.proposal_status),
            support_count=p.support_count,
            age=p.age,
            created_at=p.created_at,
        )
        for p, name in rows
    ]


@router.get("/sent-invites", response_model=list[SentInviteOut])
async def my_sent_invites(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Invites I created, so I can tell which are still outstanding."""
    rows = (
        await db.execute(
            select(Invite, Community.name)
            .join(Community, Community.id == Invite.community_id)
            .where(Invite.creator_user_id == user.id)
            .order_by(Invite.created_at.desc())
        )
    ).all()
    return [
        SentInviteOut(
            invite_id=inv.id,
            invite_code=inv.invite_code,
            community_id=inv.community_id,
            community_name=name,
            created_at=inv.created_at,
            expires_at=inv.expires_at,
            claimed=inv.claimed_by_user_id is not None,
            claimed_at=inv.claimed_at,
        )
        for inv, name in rows
    ]


@router.patch("")
async def update_me(
    body: UpdateMeRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Update display name + about. Magic-link signup gives you a random
    user_name suffix, so most real users will want to rename themselves."""
    changed = False
    if body.user_name is not None:
        candidate = body.user_name.strip()
        if not (3 <= len(candidate) <= 255):
            raise HTTPException(
                status_code=400,
                detail="user_name must be 3-255 characters",
            )
        user.user_name = candidate
        changed = True
    if body.about is not None:
        user.about = body.about[:1000]
        changed = True
    if changed:
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail="user_name already taken")
    return {
        "user_id": str(user.id),
        "user_name": user.user_name,
        "email": user.email,
        "about": user.about,
        "is_human": user.is_human,
    }
