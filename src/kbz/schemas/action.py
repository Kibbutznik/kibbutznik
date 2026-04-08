import uuid

from pydantic import BaseModel


class ActionResponse(BaseModel):
    action_id: uuid.UUID
    parent_community_id: uuid.UUID
    status: int
    name: str = ""

    model_config = {"from_attributes": True}
