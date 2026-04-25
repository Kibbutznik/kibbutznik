"""Governance-health metrics endpoint.

Single read-only route. All heavy lifting lives in MetricsService.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.database import get_db
from kbz.services.community_service import CommunityService
from kbz.services.metrics_service import MetricsService

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/community/{community_id}")
async def community_metrics(
    community_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return a `CommunityMetrics` record as JSON."""
    # Without this gate, a typo'd or stale community id returns
    # 200 with all-zero metrics — indistinguishable from a real
    # but inactive community. Make the difference visible.
    if await CommunityService(db).get(community_id) is None:
        raise HTTPException(status_code=404, detail="Community not found")
    svc = MetricsService(db)
    return (await svc.compute(community_id)).as_dict()
