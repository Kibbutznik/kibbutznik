import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CommentCreate(BaseModel):
    user_id: uuid.UUID
    # The `comments.comment_text` column is String(2000). Without a schema
    # cap the DB raises DataError → 500 for over-long payloads instead of
    # the Pydantic 422 any API client expects.
    comment_text: str = Field(min_length=1, max_length=2000)
    parent_comment_id: uuid.UUID | None = None


class CommentResponse(BaseModel):
    id: uuid.UUID
    entity_id: uuid.UUID
    entity_type: str
    user_id: uuid.UUID
    comment_text: str
    parent_comment_id: uuid.UUID | None
    score: int
    created_at: datetime
    # The viewer's own vote on this comment, or None if no session /
    # no vote cast. Lets the dashboard highlight the up/down arrow
    # the user already clicked without a separate per-comment query.
    my_value: int | None = None

    model_config = {"from_attributes": True}


class ScoreUpdate(BaseModel):
    # `user_id` is required so the server can dedupe per-user (one
    # vote per (user, comment) — see comment_votes table). Pre-fix
    # this endpoint was anonymous and added the delta blindly, so a
    # single user pressing the up arrow N times added N points.
    user_id: uuid.UUID
    # Single-step clicks. delta=0 isn't meaningful — to clear a vote,
    # click the SAME direction again (toggle off).
    delta: int = Field(ge=-1, le=1)
