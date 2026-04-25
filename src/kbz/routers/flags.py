"""Symmetric +1/-1 flags as community signal.

POST   /flags                                   — set / replace my flag
DELETE /flags/{target_kind}/{target_id}         — clear my flag
GET    /flags/{target_kind}/{target_id}         — counts + viewer's own
GET    /users/me/flags?community_id=...         — what I've flagged

Setting a flag is gated on active membership in the supplied
`community_id`. Side effect: closeness between flagger and the
target's author moves by FLAG_CLOSENESS_STEP in the value's
direction. See FlagService for details.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import (
    enforce_session_matches_body, get_current_user, require_user,
)
from kbz.database import get_db
from kbz.models.user import User
from kbz.services.flag_service import FlagService

router = APIRouter()


_KIND_TYPE = Literal["comment", "proposal", "reason", "user"]
_VALUE_TYPE = Literal[-1, 1]


class FlagSet(BaseModel):
    user_id: uuid.UUID  # the flagger
    community_id: uuid.UUID
    target_kind: _KIND_TYPE
    target_id: uuid.UUID
    value: _VALUE_TYPE


class FlagOut(BaseModel):
    id: uuid.UUID
    flagger_user_id: uuid.UUID
    community_id: uuid.UUID
    target_kind: str
    target_id: uuid.UUID
    value: int
    created_at: datetime


class FlagSummary(BaseModel):
    target_kind: str
    target_id: str
    positive: int
    negative: int
    # The viewer's own flag value, or null if not flagged / no session.
    my_value: int | None = None


@router.post("/flags", response_model=FlagOut, status_code=201)
async def set_flag(
    data: FlagSet,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    """Create or replace the caller's flag on a target.

    Re-flagging with the same value is a no-op (returns the existing
    row). Re-flagging with the OPPOSITE value flips the row and
    reverses the prior closeness delta before applying the new one.
    """
    enforce_session_matches_body(data.user_id, session_user)
    flag = await FlagService(db).set_flag(
        flagger_user_id=data.user_id,
        community_id=data.community_id,
        target_kind=data.target_kind,
        target_id=data.target_id,
        value=int(data.value),
    )
    return FlagOut(
        id=flag.id,
        flagger_user_id=flag.flagger_user_id,
        community_id=flag.community_id,
        target_kind=flag.target_kind,
        target_id=flag.target_id,
        value=flag.value,
        created_at=flag.created_at,
    )


@router.delete("/flags/{target_kind}/{target_id}", status_code=204)
async def clear_flag(
    target_kind: _KIND_TYPE,
    target_id: uuid.UUID,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove the caller's flag on this target. Reverses the prior
    closeness contribution. 404 if no flag exists for this caller."""
    removed = await FlagService(db).clear_flag(
        flagger_user_id=user.id,
        target_kind=target_kind,
        target_id=target_id,
    )
    if not removed:
        raise HTTPException(status_code=404, detail="No flag to clear")
    return None


@router.get(
    "/flags/{target_kind}/{target_id}",
    response_model=FlagSummary,
)
async def get_summary(
    target_kind: _KIND_TYPE,
    target_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    """Aggregate counts + the viewer's own value (if logged in).
    Public — no auth required for the counts so the dashboard can
    render them on every card without a roundtrip."""
    summary = await FlagService(db).get_summary(
        target_kind=target_kind,
        target_id=target_id,
        viewer_user_id=session_user.id if session_user else None,
    )
    return FlagSummary(**summary)


@router.get("/users/me/flags", response_model=list[FlagOut])
async def list_my_flags(
    community_id: uuid.UUID | None = Query(default=None),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """All flags the caller has placed, optionally scoped to one
    community. Newest first."""
    rows = await FlagService(db).list_my_flags(
        flagger_user_id=user.id, community_id=community_id,
    )
    return [
        FlagOut(
            id=r.id,
            flagger_user_id=r.flagger_user_id,
            community_id=r.community_id,
            target_kind=r.target_kind,
            target_id=r.target_id,
            value=r.value,
            created_at=r.created_at,
        )
        for r in rows
    ]
