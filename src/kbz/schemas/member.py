import uuid
from datetime import datetime

from pydantic import BaseModel


class MemberResponse(BaseModel):
    community_id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    display_name: str | None = None
    status: int
    seniority: int
    joined_at: datetime
    # Optional enrichment — populated by list_by_user so the client doesn't
    # need to follow the parent_id chain to group memberships by tree root.
    community_name: str | None = None
    community_parent_id: uuid.UUID | None = None
    community_root_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}
