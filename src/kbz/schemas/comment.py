import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CommentCreate(BaseModel):
    user_id: uuid.UUID
    comment_text: str
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
    # A vote is +1 or -1. Leaving delta unbounded lets any caller POST
    # delta=1_000_000 and shoot a comment to the top of the sort.
    delta: int = Field(..., ge=-1, le=1)
