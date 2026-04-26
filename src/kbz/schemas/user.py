import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class UserCreate(BaseModel):
    user_name: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    about: str = Field(default="", max_length=4000)
    wallet_address: str = Field(default="", max_length=255)

    @field_validator("user_name")
    @classmethod
    def _user_name_not_whitespace_only(cls, v: str) -> str:
        # min_length=3 lets "   " (3 spaces) through. The user then
        # appears blank in member lists, audit log entries, comment
        # threads, and the bot dropdown — and any "user_name" cookie
        # claim or magic-link greeting renders awkwardly. Reject at
        # schema time.
        if not v.strip():
            raise ValueError(
                "user_name must contain non-whitespace characters"
            )
        return v


class UserResponse(BaseModel):
    id: uuid.UUID
    user_name: str
    about: str
    wallet_address: str
    created_at: datetime

    model_config = {"from_attributes": True}
