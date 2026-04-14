import uuid
from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, Text, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    container_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    community_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=True, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    prev_artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_plan: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_artifacts_container", "container_id", "status"),
        Index("idx_artifacts_prev", "prev_artifact_id"),
    )
