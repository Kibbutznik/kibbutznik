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

    model_config = {"from_attributes": True}


class ScoreUpdate(BaseModel):
    delta: int  # +1 or -1
