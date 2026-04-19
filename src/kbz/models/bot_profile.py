"""BotProfile — per-user, per-community delegation config.

A logged-in human can enable a `BotProfile` in any kibbutz they belong
to. When active, a background runner takes turns on their behalf using
the LLM, constrained by the fields below. The bot acts as the USER
(same user_id on every write), so a human can jump in at any time and
the audit trail stays clean.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


class BotProfile(Base):
    __tablename__ = "bot_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    community_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    orientation: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pragmatist'"),
        default="pragmatist",
    )
    initiative: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5"), default=5,
    )
    agreeableness: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("5"), default=5,
    )
    goals: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''"), default="",
    )
    boundaries: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''"), default="",
    )
    approval_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'autonomous'"),
        default="autonomous",
    )
    turn_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("300"), default=300,
    )
    last_turn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )


# Allowed orientation values — referenced by router validation + bot runner
ORIENTATIONS = (
    "producer",
    "consensus",
    "devils_advocate",
    "idealist",
    "pragmatist",
    "diplomat",
)
