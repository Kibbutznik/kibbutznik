import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import enforce_session_matches_body, get_current_user
from kbz.database import get_db
from kbz.models.user import User
from kbz.schemas.comment import CommentCreate, CommentResponse, ScoreUpdate
from kbz.services.comment_service import CommentService

router = APIRouter()


# Only "proposal" and "community" are wired through the service/ingestor.
# Anything else silently creates an orphan row that no UI surfaces.
_ALLOWED_ENTITY_TYPES = {"proposal", "community"}


def _require_entity_type(entity_type: str) -> str:
    if entity_type not in _ALLOWED_ENTITY_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"entity_type must be one of {sorted(_ALLOWED_ENTITY_TYPES)}",
        )
    return entity_type


@router.post("/entities/{entity_type}/{entity_id}/comments", response_model=CommentResponse, status_code=201)
async def add_comment(
    entity_type: str,
    entity_id: uuid.UUID,
    data: CommentCreate,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    enforce_session_matches_body(data.user_id, session_user)
    _require_entity_type(entity_type)
    svc = CommentService(db)
    return await svc.add_comment(entity_id, entity_type, data)


@router.get("/entities/{entity_type}/{entity_id}/comments", response_model=list[CommentResponse])
async def get_comments(
    entity_type: str,
    entity_id: uuid.UUID,
    limit: int | None = Query(None, ge=1, le=500),
    after: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    _require_entity_type(entity_type)
    svc = CommentService(db)
    return await svc.get_comments(entity_id, entity_type, limit=limit, after=after)


@router.post("/comments/{comment_id}/score")
async def update_score(comment_id: uuid.UUID, data: ScoreUpdate, db: AsyncSession = Depends(get_db)):
    svc = CommentService(db)
    await svc.update_score(comment_id, data.delta)
    return {"status": "updated"}
