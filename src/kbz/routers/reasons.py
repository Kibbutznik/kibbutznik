"""Deliberation tree under a proposal — Reason CRUD.

POST /proposals/{id}/reasons   — create a new pro/con reason
GET  /proposals/{id}/reasons   — flat list of active reasons
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import enforce_session_matches_body, get_current_user
from kbz.database import get_db
from kbz.models.user import User
from kbz.schemas.reason import ReasonCreate, ReasonResponse
from kbz.services.reason_service import ReasonService

router = APIRouter()


@router.post(
    "/proposals/{proposal_id}/reasons",
    response_model=ReasonResponse,
    status_code=201,
)
async def create_reason(
    proposal_id: uuid.UUID,
    data: ReasonCreate,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    enforce_session_matches_body(data.user_id, session_user)
    return await ReasonService(db).create(proposal_id, data)


@router.get(
    "/proposals/{proposal_id}/reasons",
    response_model=list[ReasonResponse],
)
async def list_reasons(
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    # We don't pre-check proposal existence here — the empty list
    # is a fine answer for "no deliberation yet" AND for "no such
    # proposal" because the dashboard distinguishes those cases via
    # GET /proposals/{id} (which already 404s correctly).
    return await ReasonService(db).list_for_proposal(proposal_id)
