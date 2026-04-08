import uuid
from datetime import datetime, timezone

from sqlalchemy import Index, Integer, String, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


class Community(Base):
    __tablename__ = "communities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    parent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, default=uuid.UUID("00000000-0000-0000-0000-000000000000"))
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="No Name")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_communities_parent", "parent_id"),
    )
