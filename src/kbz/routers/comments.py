import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from kbz.auth_deps import enforce_session_matches_body, get_current_user
from kbz.database import get_db
from kbz.models.user import User
from kbz.schemas.comment import CommentCreate, CommentResponse, ScoreUpdate
from kbz.services.comment_service import CommentService

router = APIRouter()


@router.post("/entities/{entity_type}/{entity_id}/comments", response_model=CommentResponse, status_code=201)
async def add_comment(
    entity_type: str,
    entity_id: uuid.UUID,
    data: CommentCreate,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    enforce_session_matches_body(data.user_id, session_user)
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
    svc = CommentService(db)
    return await svc.get_comments(entity_id, entity_type, limit=limit, after=after)


@router.get("/comments/{comment_id}/replies", response_model=list[CommentResponse])
async def get_replies(
    comment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return direct child comments of `comment_id`. Without this
    endpoint replies posted via `parent_comment_id` were invisible —
    the entity-comments listing only returns top-level rows, so a
    threaded UI had no way to fetch the children at all.
    """
    svc = CommentService(db)
    return await svc.get_replies(comment_id)


@router.post("/comments/{comment_id}/score")
async def update_score(
    comment_id: uuid.UUID,
    data: ScoreUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Score a comment by ±delta. Returns the new score so clients
    can update the in-place number without re-fetching the entire
    proposal/comment tree."""
    svc = CommentService(db)
    new_score = await svc.update_score(comment_id, data.delta)
    return {"id": str(comment_id), "score": new_score}
