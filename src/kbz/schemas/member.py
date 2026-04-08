import uuid
from datetime import datetime

from pydantic import BaseModel


class MemberResponse(BaseModel):
    community_id: uuid.UUID
    user_id: uuid.UUID
    status: int
    seniority: int
    joined_at: datetime

    model_config = {"from_attributes": True}
