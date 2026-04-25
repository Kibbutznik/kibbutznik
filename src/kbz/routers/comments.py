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
    include_replies: bool = Query(
        True,
        description=(
            "When true (default) the response includes the full tree as a "
            "flat list — clients group by parent_comment_id to render "
            "threading. Pass false for compact views that only need roots."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    _require_entity_type(entity_type)
    svc = CommentService(db)
    rows = await svc.get_comments(
        entity_id, entity_type,
        limit=limit, after=after, include_replies=include_replies,
    )
    # Stamp each row with the viewer's own vote so the dashboard
    # can highlight the up/down arrow they already cast. Bulk lookup
    # so a 100-comment thread is one extra query, not 100.
    if session_user is not None and rows:
        my_votes = await svc.get_my_votes_bulk(
            [r.id for r in rows], session_user.id,
        )
    else:
        my_votes = {}
    return [
        CommentResponse(
            id=r.id,
            entity_id=r.entity_id,
            entity_type=r.entity_type,
            user_id=r.user_id,
            comment_text=r.comment_text,
            parent_comment_id=r.parent_comment_id,
            score=r.score,
            created_at=r.created_at,
            my_value=my_votes.get(r.id),
        )
        for r in rows
    ]


@router.post("/comments/{comment_id}/score")
async def update_score(
    comment_id: uuid.UUID,
    data: ScoreUpdate,
    db: AsyncSession = Depends(get_db),
    session_user: User | None = Depends(get_current_user),
):
    """Toggle-aware vote on a comment.

    Behavior is per-user (\\`comment_votes\\` table, one row per (user,
    comment)). Pre-fix this endpoint was anonymous and blindly added
    the delta to comments.score, so a single user pressing up 20
    times added 20 points. Now:

    - Click in a new direction → INSERT, score moves by 1.
    - Click in the same direction as your prior vote → DELETE
      (toggle off), score moves by -1 in that direction.
    - Click the opposite direction → flip, score moves by 2.

    Response carries the comment id, the NEW total score, and your
    new vote value (or null if you toggled off). The client updates
    only the up/down arrow + counter — no modal re-fetch.
    """
    enforce_session_matches_body(data.user_id, session_user)
    svc = CommentService(db)
    new_score, my_value = await svc.cast_vote(
        comment_id, data.user_id, data.delta,
    )
    return {
        "status": "updated",
        "id": str(comment_id),
        "score": new_score,
        "my_value": my_value,
    }
