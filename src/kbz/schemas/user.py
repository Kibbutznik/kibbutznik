import uuid
from datetime import datetime

from pydantic import BaseModel


class UserCreate(BaseModel):
    user_name: str
    password: str
    about: str = ""
    wallet_address: str = ""


class UserResponse(BaseModel):
    id: uuid.UUID
    user_name: str
    about: str
    wallet_address: str
    created_at: datetime

    model_config = {"from_attributes": True}
