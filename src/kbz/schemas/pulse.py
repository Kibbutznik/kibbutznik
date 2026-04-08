import uuid
from datetime import datetime

from pydantic import BaseModel


class PulseResponse(BaseModel):
    id: uuid.UUID
    community_id: uuid.UUID
    status: int
    support_count: int
    threshold: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PulseSupportCreate(BaseModel):
    user_id: uuid.UUID
