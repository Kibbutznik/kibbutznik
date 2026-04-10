import uuid
from datetime import datetime

from pydantic import BaseModel


class CommunityCreate(BaseModel):
    name: str
    founder_user_id: uuid.UUID
    parent_id: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
    # Briefing written onto the root container so agents know what kind of
    # content this community is supposed to produce. Only used for root
    # communities (parent_id == ZERO_UUID); ignored for sub-actions.
    initial_artifact_mission: str | None = None


class CommunityResponse(BaseModel):
    id: uuid.UUID
    parent_id: uuid.UUID
    name: str
    status: int
    member_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class CommunityVariablesResponse(BaseModel):
    community_id: uuid.UUID
    variables: dict[str, str]
