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
    prev_content: str | None = None
    # Computed enrichment fields. `promote_threshold` is the member
    # count needed to move OutThere → OnTheAir (ProposalSupport %).
    # `decide_threshold` is the per-type threshold for execution
    # when OnTheAir (e.g. Funding %, Membership %, etc). Both are
    # None only for brand-new proposals fetched before enrichment.
    promote_threshold: int | None = None
    decide_threshold: int | None = None
    user_name: str | None = None
    display_name: str | None = None

    model_config = {"from_attributes": True}


class ProposalEdit(BaseModel):
    user_id: uuid.UUID
    proposal_text: str | None = None
    val_text: str | None = None


class SupportCreate(BaseModel):
    user_id: uuid.UUID
