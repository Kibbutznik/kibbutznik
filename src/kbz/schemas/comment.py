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
    # Clamped to a single step either direction — callers have been
    # observed sending delta=1 per click, and unbounded ints let anyone
    # pump a comment's score by thousands in a single POST.
    delta: int = Field(ge=-1, le=1)
