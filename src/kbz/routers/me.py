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
from kbz.models.bot_profile import ORIENTATIONS, BotProfile
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
    community_parent_id: uuid.UUID | None = None
    community_root_id: uuid.UUID | None = None


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
    """Active memberships for the logged-in user across all communities.

    Each row is enriched with community_parent_id + community_root_id so
    the dashboard can render action-communities as a tree under their
    root kibbutz.
    """
    from kbz.services.member_service import MemberService
    rows = await MemberService(db).list_by_user(user.id)
    rows.sort(key=lambda r: r.joined_at, reverse=True)
    return [
        MembershipOut(
            community_id=r.community_id,
            community_name=r.community_name,
            joined_at=r.joined_at,
            seniority=r.seniority,
            status=int(r.status),
            community_parent_id=r.community_parent_id,
            community_root_id=r.community_root_id,
        )
        for r in rows
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


# ═══════════════════════════════════════════════════════════════════
# Bot profiles — delegate the user's participation to an AI proxy
# ═══════════════════════════════════════════════════════════════════

class BotProfileOut(BaseModel):
    community_id: uuid.UUID
    community_name: str
    active: bool
    display_name: str | None
    orientation: str
    initiative: int
    agreeableness: int
    goals: str
    boundaries: str
    approval_mode: str
    turn_interval_seconds: int
    last_turn_at: datetime | None


class BotProfileUpsert(BaseModel):
    """All fields optional on update — only provided ones change."""
    active: bool | None = None
    display_name: str | None = None
    orientation: str | None = None
    initiative: int | None = None
    agreeableness: int | None = None
    goals: str | None = None
    boundaries: str | None = None
    approval_mode: str | None = None
    turn_interval_seconds: int | None = None


def _validate_bot_fields(profile: BotProfile) -> None:
    if profile.orientation not in ORIENTATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"orientation must be one of {ORIENTATIONS}",
        )
    for field in ("initiative", "agreeableness"):
        val = getattr(profile, field)
        if not (1 <= val <= 10):
            raise HTTPException(status_code=400, detail=f"{field} must be 1..10")
    if profile.approval_mode not in ("autonomous", "review"):
        raise HTTPException(
            status_code=400,
            detail="approval_mode must be 'autonomous' or 'review'",
        )
    if not (30 <= profile.turn_interval_seconds <= 86400):
        raise HTTPException(
            status_code=400,
            detail="turn_interval_seconds must be 30..86400",
        )


@router.get("/bots", response_model=list[BotProfileOut])
async def my_bots(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(BotProfile, Community.name)
            .join(Community, Community.id == BotProfile.community_id)
            .where(BotProfile.user_id == user.id)
            .order_by(BotProfile.updated_at.desc())
        )
    ).all()
    return [
        BotProfileOut(
            community_id=bp.community_id,
            community_name=name,
            active=bp.active,
            display_name=bp.display_name,
            orientation=bp.orientation,
            initiative=bp.initiative,
            agreeableness=bp.agreeableness,
            goals=bp.goals,
            boundaries=bp.boundaries,
            approval_mode=bp.approval_mode,
            turn_interval_seconds=bp.turn_interval_seconds,
            last_turn_at=bp.last_turn_at,
        )
        for bp, name in rows
    ]


@router.put("/bots/{community_id}", response_model=BotProfileOut)
async def upsert_bot(
    community_id: uuid.UUID,
    body: BotProfileUpsert,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or update the bot profile for a community.

    The caller must already be an active member of the community — a
    bot can't deputize for a seat the user doesn't have.
    """
    # Membership check
    is_member = (
        await db.execute(
            select(Member).where(
                Member.community_id == community_id,
                Member.user_id == user.id,
                Member.status == MemberStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()
    if is_member is None:
        raise HTTPException(
            status_code=403,
            detail="You must be an active member to activate a bot here.",
        )

    existing = (
        await db.execute(
            select(BotProfile).where(
                BotProfile.user_id == user.id,
                BotProfile.community_id == community_id,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        # Initialize with explicit Python-side defaults. Alembic
        # `server_default` only kicks in at INSERT time (after flush);
        # we run `_validate_bot_fields` BEFORE flush, so each field
        # has to carry a value from the start.
        profile = BotProfile(
            user_id=user.id,
            community_id=community_id,
            active=True,
            display_name=None,
            orientation="pragmatist",
            initiative=5,
            agreeableness=5,
            goals="",
            boundaries="",
            approval_mode="autonomous",
            turn_interval_seconds=300,
        )
        db.add(profile)
    else:
        profile = existing

    # Apply only provided fields
    for field in (
        "active", "display_name", "orientation", "initiative",
        "agreeableness", "goals", "boundaries", "approval_mode",
        "turn_interval_seconds",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(profile, field, val)
    profile.updated_at = datetime.now(timezone.utc)
    _validate_bot_fields(profile)
    await db.commit()
    await db.refresh(profile)

    community = (
        await db.execute(select(Community).where(Community.id == community_id))
    ).scalar_one()
    return BotProfileOut(
        community_id=profile.community_id,
        community_name=community.name,
        active=profile.active,
        display_name=profile.display_name,
        orientation=profile.orientation,
        initiative=profile.initiative,
        agreeableness=profile.agreeableness,
        goals=profile.goals,
        boundaries=profile.boundaries,
        approval_mode=profile.approval_mode,
        turn_interval_seconds=profile.turn_interval_seconds,
        last_turn_at=profile.last_turn_at,
    )


@router.delete("/bots/{community_id}", status_code=204)
async def delete_bot(
    community_id: uuid.UUID,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove the bot profile entirely. Equivalent to setting active=false
    then clearing the config — we just drop the row."""
    await db.execute(
        BotProfile.__table__.delete().where(
            BotProfile.user_id == user.id,
            BotProfile.community_id == community_id,
        )
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════
# API tokens — long-lived bearer tokens for external bots / scripts
# ═══════════════════════════════════════════════════════════════════

class ApiTokenCreate(BaseModel):
    name: str  # user-chosen label, e.g. "claude-code-skill"


class ApiTokenOut(BaseModel):
    """Shape returned from LIST + DELETE. The raw token value is
    deliberately NOT in here — it's only in the one-time CREATE
    response below. Once lost, mint a new token."""
    id: uuid.UUID
    name: str | None
    created_at: datetime
    expires_at: datetime


class ApiTokenCreateOut(ApiTokenOut):
    # The raw, un-hashed token. Shown exactly once. If the user loses
    # it they must create a new one and revoke this one.
    token: str


@router.post("/tokens", response_model=ApiTokenCreateOut)
async def create_api_token(
    body: ApiTokenCreate,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Mint a personal API token. The `token` value in the response is
    visible exactly once — the user must paste it into their bot's
    config immediately. We only store SHA-256(token) on our side."""
    from kbz.services.auth_service import AuthService
    svc = AuthService(db)
    try:
        issued = await svc.issue_api_token(user, name=body.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.commit()
    # Fetch the row we just inserted to return full metadata
    from kbz.models.auth import AuthToken
    row = (
        await db.execute(select(AuthToken).where(AuthToken.id == issued.token_id))
    ).scalar_one()
    return ApiTokenCreateOut(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        expires_at=row.expires_at,
        token=issued.raw,
    )


@router.get("/tokens", response_model=list[ApiTokenOut])
async def list_api_tokens(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    from kbz.services.auth_service import AuthService
    rows = await AuthService(db).list_api_tokens(user)
    return [
        ApiTokenOut(
            id=r.id, name=r.name,
            created_at=r.created_at, expires_at=r.expires_at,
        )
        for r in rows
    ]


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_api_token(
    token_id: uuid.UUID,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    from kbz.services.auth_service import AuthService
    revoked = await AuthService(db).revoke_api_token(user, token_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Token not found")
    await db.commit()
