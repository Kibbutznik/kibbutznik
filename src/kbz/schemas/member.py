import uuid
from datetime import datetime

from pydantic import BaseModel


class CommunityMemberResponse(BaseModel):
    """A member row for `/communities/{id}/members` — caller already knows
    which community this is, so the community_* tree fields are omitted."""

    community_id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    display_name: str | None = None
    status: int
    seniority: int
    joined_at: datetime

    model_config = {"from_attributes": True}


class UserMembershipResponse(BaseModel):
    """A membership row for `/users/{id}/communities` — each row spans a
    different community, so it carries community_name plus the tree-root
    id so clients can group by root kibbutz without walking parent_id."""

    community_id: uuid.UUID
    user_id: uuid.UUID
    user_name: str | None = None
    status: int
    seniority: int
    joined_at: datetime
    community_name: str | None = None
    community_parent_id: uuid.UUID | None = None
    community_root_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}
