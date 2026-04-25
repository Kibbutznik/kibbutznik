"""Per-user notification inbox.

GET    /users/me/notifications              — list (paginated, optional unread filter)
GET    /users/me/notifications/unread-count — number for the badge
PATCH  /users/me/notifications/{id}/read    — mark one read
POST   /users/me/notifications/read-all     — mark all read

All routes require a session — there is no anonymous inbox.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import require_user
from kbz.database import get_db
from kbz.models.user import User
from kbz.services.notification_service import NotificationService

router = APIRouter(prefix="/users/me/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: uuid.UUID
    community_id: uuid.UUID | None
    kind: str
    payload: dict
    created_at: datetime
    read_at: datetime | None


class UnreadCountOut(BaseModel):
    unread: int


@router.get("", response_model=list[NotificationOut])
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[NotificationOut]:
    rows = await NotificationService(db).list_for_user(
        user.id, unread_only=unread_only, limit=limit, offset=offset,
    )
    return [
        NotificationOut(
            id=r.id,
            community_id=r.community_id,
            kind=r.kind,
            payload=r.payload_json or {},
            created_at=r.created_at,
            read_at=r.read_at,
        )
        for r in rows
    ]


@router.get("/unread-count", response_model=UnreadCountOut)
async def unread_count(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> UnreadCountOut:
    return UnreadCountOut(unread=await NotificationService(db).unread_count(user.id))


@router.patch("/{notification_id}/read", response_model=dict)
async def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Mark one notification as read. 404 if it doesn't belong to
    this user, OR if it was already read — we collapse those two
    cases on purpose so a fishing client can't probe foreign ids."""
    flipped = await NotificationService(db).mark_read(user.id, notification_id)
    if not flipped:
        raise HTTPException(status_code=404, detail="notification not found")
    await db.commit()
    return {"ok": True}


@router.post("/read-all", response_model=dict)
async def mark_all_read(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    n = await NotificationService(db).mark_all_read(user.id)
    await db.commit()
    return {"marked_read": n}
