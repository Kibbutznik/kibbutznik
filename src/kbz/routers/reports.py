"""Moderation reports — file, list, resolve.

POST   /reports                        — file a new report
GET    /communities/{id}/reports       — list (filterable by status)
PATCH  /reports/{id}                   — uphold or dismiss

All endpoints require an active session. Filing requires
membership in the community being reported in. Resolving
likewise — any active member can resolve a report in their own
community. Heavier moderation roles are a follow-up.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import enforce_session_matches_body, get_current_user, require_user
from kbz.database import get_db
from kbz.models.community import Community
from kbz.models.report import (
    STATUS_DISMISSED, STATUS_OPEN, STATUS_TO_NAME, STATUS_UPHELD,
    TARGET_KINDS, Report,
)
from kbz.models.user import User
from kbz.services.community_service import CommunityService
from kbz.services.member_service import MemberService

router = APIRouter()


class ReportCreate(BaseModel):
    user_id: uuid.UUID  # the reporter
    community_id: uuid.UUID
    target_kind: Literal["comment", "proposal", "reason", "user"]
    target_id: uuid.UUID
    # 1-2000 chars. Empty reports are noise; a novel-length one
    # belongs in a proposal, not a moderation flag.
    reason_text: str = Field(min_length=1, max_length=2000)


class ReportResolve(BaseModel):
    status: Literal["upheld", "dismissed"]


class ReportOut(BaseModel):
    id: uuid.UUID
    community_id: uuid.UUID
    reporter_user_id: uuid.UUID
    target_kind: str
    target_id: uuid.UUID
    reason_text: str
    status: str  # "open" | "upheld" | "dismissed"
    resolver_user_id: uuid.UUID | None
    resolved_at: datetime | None
    created_at: datetime


def _to_out(r: Report) -> ReportOut:
    return ReportOut(
        id=r.id,
        community_id=r.community_id,
        reporter_user_id=r.reporter_user_id,
        target_kind=r.target_kind,
        target_id=r.target_id,
        reason_text=r.reason_text,
        status=STATUS_TO_NAME.get(r.status, str(r.status)),
        resolver_user_id=r.resolver_user_id,
        resolved_at=r.resolved_at,
        created_at=r.created_at,
    )


@router.post("/reports", response_model=ReportOut, status_code=201)
async def file_report(
    data: ReportCreate,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    enforce_session_matches_body(data.user_id, session_user)
    if data.target_kind not in TARGET_KINDS:
        raise HTTPException(
            status_code=422, detail=f"target_kind must be one of {TARGET_KINDS}",
        )

    # The community must exist.
    if await CommunityService(db).get(data.community_id) is None:
        raise HTTPException(status_code=404, detail="Community not found")

    # Reporter must be an active member of that community. A non-
    # member couldn't observe what they're reporting on with
    # confidence, and letting strangers fire reports invites brigading.
    if not await MemberService(db).is_active_member(
        data.community_id, data.user_id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Only active members of the community can file reports here",
        )

    # Self-reports against your own user_id are nonsensical noise.
    if data.target_kind == "user" and data.target_id == data.user_id:
        raise HTTPException(
            status_code=400, detail="Cannot file a moderation report against yourself",
        )

    report = Report(
        id=uuid.uuid4(),
        community_id=data.community_id,
        reporter_user_id=data.user_id,
        target_kind=data.target_kind,
        target_id=data.target_id,
        reason_text=data.reason_text,
        status=STATUS_OPEN,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return _to_out(report)


@router.get(
    "/communities/{community_id}/reports",
    response_model=list[ReportOut],
)
async def list_reports(
    community_id: uuid.UUID,
    status: Literal["open", "upheld", "dismissed"] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """List reports in a community. Visible to active members only —
    unresolved reports often name-and-shame, so a stranger
    shouldn't be able to scrape them."""
    if await CommunityService(db).get(community_id) is None:
        raise HTTPException(status_code=404, detail="Community not found")
    if not await MemberService(db).is_active_member(community_id, user.id):
        raise HTTPException(
            status_code=403,
            detail="Only active members of this community can view its reports",
        )
    stmt = select(Report).where(Report.community_id == community_id)
    if status is not None:
        wanted = {
            "open": STATUS_OPEN, "upheld": STATUS_UPHELD,
            "dismissed": STATUS_DISMISSED,
        }[status]
        stmt = stmt.where(Report.status == wanted)
    stmt = stmt.order_by(Report.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_out(r) for r in rows]


@router.patch("/reports/{report_id}", response_model=ReportOut)
async def resolve_report(
    report_id: uuid.UUID,
    body: ReportResolve,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Move a report from OPEN → UPHELD or OPEN → DISMISSED. Only
    active members of the report's community can resolve it; a
    report can only be resolved once (no re-flipping)."""
    row = (
        await db.execute(select(Report).where(Report.id == report_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if not await MemberService(db).is_active_member(
        row.community_id, user.id,
    ):
        raise HTTPException(
            status_code=403,
            detail="Only active members of the report's community can resolve it",
        )
    if row.status != STATUS_OPEN:
        raise HTTPException(
            status_code=400,
            detail=f"Report is already {STATUS_TO_NAME[row.status]} — cannot re-resolve",
        )
    new_status = STATUS_UPHELD if body.status == "upheld" else STATUS_DISMISSED
    await db.execute(
        update(Report)
        .where(Report.id == report_id, Report.status == STATUS_OPEN)
        .values(
            status=new_status,
            resolver_user_id=user.id,
            resolved_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()
    row = (
        await db.execute(select(Report).where(Report.id == report_id))
    ).scalar_one()
    return _to_out(row)
