"""Invite endpoints.

POST /communities/{community_id}/invites
    → Create a new invite link. Requires login.
POST /invites/claim
    → Consume a code + email. Creates-or-fetches the user, files a
      Membership proposal, returns a magic-link verify URL so the
      invited human can activate their session.
GET  /invites/{invite_code}
    → Preview: returns the community name + expires_at without
      consuming. Lets the viewer render a proper "join X?" screen
      before the user commits.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import require_user
from kbz.config import settings
from kbz.database import get_db
from kbz.models.auth import Invite
from kbz.models.community import Community
from kbz.models.user import User
from kbz.services.auth_service import AuthService
from kbz.services.invite_service import InviteService
from kbz.services.member_service import MemberService
from kbz.services.event_bus import event_bus

router = APIRouter(tags=["invites"])


class CreateInviteResponse(BaseModel):
    invite_id: str
    code: str
    expires_at: str
    url: str   # full path the human should share, e.g. "/invite/<code>"


class ClaimRequest(BaseModel):
    invite_code: str
    email: EmailStr


class ClaimResponse(BaseModel):
    user_id: str
    community_id: str
    membership_proposal_id: str
    verify_link: str | None = None  # dev-mode convenience


class InvitePreview(BaseModel):
    invite_code: str
    community_id: str
    community_name: str
    expires_at: str
    claimed: bool


@router.post(
    "/communities/{community_id}/invites",
    response_model=CreateInviteResponse,
)
async def create_invite(
    community_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> CreateInviteResponse:
    # Community-existence check goes first so callers still get a 404
    # for bogus ids instead of a 403 that leaks nothing.
    exists = (
        await db.execute(
            select(Community.id).where(Community.id == community_id)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="community not found")
    # Only active members of the community may mint invites. Without
    # this check any logged-in user could spam invite codes into any
    # community, bypassing the social-proof model.
    if not await MemberService(db).is_active_member(community_id, user.id):
        raise HTTPException(
            status_code=403,
            detail="Only active members can create invites",
        )
    try:
        issued = await InviteService(db).create(
            community_id=community_id, creator_user_id=user.id
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await db.commit()
    return CreateInviteResponse(
        invite_id=str(issued.invite_id),
        code=issued.code,
        expires_at=issued.expires_at.isoformat(),
        url=f"/invite/{issued.code}",
    )


@router.get("/invites/{invite_code}", response_model=InvitePreview)
async def preview_invite(
    invite_code: str, db: AsyncSession = Depends(get_db)
) -> InvitePreview:
    row = (
        await db.execute(select(Invite).where(Invite.invite_code == invite_code))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="invite not found")
    community = (
        await db.execute(select(Community).where(Community.id == row.community_id))
    ).scalar_one_or_none()
    return InvitePreview(
        invite_code=row.invite_code,
        community_id=str(row.community_id),
        community_name=community.name if community else "Unknown Community",
        expires_at=row.expires_at.isoformat(),
        claimed=row.claimed_by_user_id is not None,
    )


@router.post("/invites/claim", response_model=ClaimResponse)
async def claim_invite(
    body: ClaimRequest,
    db: AsyncSession = Depends(get_db),
) -> ClaimResponse:
    """Consume an invite, create the user, file a Membership proposal, and
    return a magic-link verify URL (dev mode) so the user can sign in.
    """
    svc = InviteService(db)
    try:
        claimed = await svc.claim(
            invite_code=body.invite_code, email=body.email
        )
    except ValueError as e:
        msg = str(e)
        code = status.HTTP_400_BAD_REQUEST
        if msg == "invite not found":
            code = status.HTTP_404_NOT_FOUND
        elif msg == "user is already a member of this community":
            code = status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=msg)

    # Issue a magic link so the user can sign in immediately
    auth_svc = AuthService(db)
    magic = await auth_svc.issue_magic_link(claimed.user)
    await db.commit()

    # Fan out events so the TKGIngestor + viewer tick can pick up the
    # new proposal + member prospect. This matches what the existing
    # proposal_service.create does when agents submit.
    await event_bus.emit(
        "proposal.created",
        community_id=claimed.community_id,
        user_id=claimed.user.id,
        proposal_id=claimed.membership_proposal_id,
        proposal_type="Membership",
        proposal_text=f"{claimed.user.user_name} applied via invite link",
    )

    verify: str | None = None
    if settings.auth_dev_expose_magic_link:
        verify = f"/auth/verify?token={magic.raw}"
    return ClaimResponse(
        user_id=str(claimed.user.id),
        community_id=str(claimed.community_id),
        membership_proposal_id=str(claimed.membership_proposal_id),
        verify_link=verify,
    )
