"""Pydantic schemas for the Reason deliberation tree."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReasonCreate(BaseModel):
    user_id: uuid.UUID
    # Stance is constrained to "pro" | "con" so the dashboard's
    # two-column layout never has to handle a surprise third value.
    # Adding "neutral" later would mean expanding this Literal.
    stance: Literal["pro", "con"]
    # 1-4000 chars matches the spirit of the proposal_text cap (10k)
    # but tighter — a Reason is a single claim, not an essay. Empty
    # claim text is rejected so the inbox doesn't fill with [no
    # reason given] rows.
    claim_text: str = Field(min_length=1, max_length=4000)
    # When set, this Reason is a counter-reply to another. The
    # service layer enforces opposite stance vs the parent so the
    # tree structure carries genuine debate, not "yes, and"
    # echoes.
    parent_reason_id: uuid.UUID | None = None


class ReasonResponse(BaseModel):
    id: uuid.UUID
    proposal_id: uuid.UUID
    user_id: uuid.UUID
    stance: str
    claim_text: str
    parent_reason_id: uuid.UUID | None
    status: int
    created_at: datetime

    model_config = {"from_attributes": True}
