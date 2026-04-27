import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class CommunityCreate(BaseModel):
    # communities.name is String(255); an unbounded schema field lets
    # 300-char names through to the DB layer and 500s on DataError.
    # min_length=1 alone lets "   " through — a whitespace-only name
    # creates a community that's invisible in the UI list (rendered as
    # empty space) but still consumes a row. Strip + reject in the
    # validator below so the input that reaches CommunityService.create
    # is always meaningful text.
    name: str = Field(min_length=1, max_length=255)
    founder_user_id: uuid.UUID

    @field_validator("name")
    @classmethod
    def _name_not_whitespace_only(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must contain non-whitespace characters")
        return v
    parent_id: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
    # Briefing written onto the root container so agents know what kind of
    # content this community is supposed to produce. Only used for root
    # communities (parent_id == ZERO_UUID); ignored for sub-actions.
    initial_artifact_mission: str | None = Field(default=None, max_length=4000)
    # If True, sets `variables['Financial'] = 'internal'` at creation
    # time so the founder doesn't need to file a ChangeVariable
    # proposal against themselves at t=0. Default False keeps
    # existing API callers (simulation, agents) non-financial.
    enable_financial: bool = False


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
