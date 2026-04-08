import uuid
from datetime import datetime

from pydantic import BaseModel


class ProposalCreate(BaseModel):
    user_id: uuid.UUID
    proposal_type: str
    proposal_text: str = ""
    val_uuid: uuid.UUID | None = None
    val_text: str = ""


class ProposalResponse(BaseModel):
    id: uuid.UUID
    community_id: uuid.UUID
    user_id: uuid.UUID
    proposal_type: str
    proposal_status: str
    proposal_text: str
    val_uuid: uuid.UUID | None
    val_text: str | None
    pulse_id: uuid.UUID | None
    age: int
    support_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ProposalEdit(BaseModel):
    user_id: uuid.UUID
    proposal_text: str | None = None
    val_text: str | None = None


class SupportCreate(BaseModel):
    user_id: uuid.UUID
