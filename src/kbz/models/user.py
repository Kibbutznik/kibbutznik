import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Index, String, DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    about: Mapped[str] = mapped_column(String(1000), nullable=True, default="")
    wallet_address: Mapped[str] = mapped_column(String(255), nullable=True, default="")
    # Human-auth fields (Track C). NULL/False for agent users. Populated
    # when a human claims a magic link or an invite.
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    is_human: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        # Partial unique index on lower(email) — closes the
        # magic-link race window where two concurrent get_or_create_human
        # calls for the same address would land two User rows. Bot/test
        # accounts have email IS NULL and are exempt. Indexed via LOWER()
        # so "Alice@x.com" can't bypass the check by varying case.
        Index(
            "ix_users_email_lower_unique",
            func.lower(email),
            unique=True,
            postgresql_where=email.is_not(None),
        ),
    )
