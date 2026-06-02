import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class ContactCreate(BaseModel):
    # Required. Capped so a single submission can't dump unbounded text.
    message: str = Field(min_length=1, max_length=5000)
    # Optional — a visitor can leave a message anonymously. Encouraged so
    # we can reply.
    email: EmailStr | None = None
    name: str | None = Field(default=None, max_length=120)
    # Honeypot. A real human leaves this empty (it's hidden via CSS); bots
    # that fill every field trip it. Filled => silently dropped with a 200
    # so the bot believes it succeeded.
    website: str | None = Field(default=None, max_length=200)


class ContactResponse(BaseModel):
    ok: bool


class SendMailIn(BaseModel):
    to: EmailStr
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)
    reply_to: EmailStr | None = None


class SendMailResponse(BaseModel):
    ok: bool
    detail: str | None = None


class ContactMessageOut(BaseModel):
    id: uuid.UUID
    name: str | None
    email: str | None
    message: str
    ip: str | None
    user_agent: str | None
    handled: bool
    created_at: datetime

    model_config = {"from_attributes": True}
