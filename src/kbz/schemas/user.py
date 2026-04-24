import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    user_name: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    about: str = Field(default="", max_length=4000)
    wallet_address: str = Field(default="", max_length=255)


class UserResponse(BaseModel):
    id: uuid.UUID
    user_name: str
    about: str
    wallet_address: str
    created_at: datetime

    model_config = {"from_attributes": True}
