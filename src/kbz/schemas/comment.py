import uuid
from datetime import datetime

from pydantic import BaseModel


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
    delta: int  # +1 or -1
