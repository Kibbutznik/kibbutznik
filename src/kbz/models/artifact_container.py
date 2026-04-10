import uuid
from datetime import datetime

from sqlalchemy import Index, Integer, String, Text, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


class ArtifactContainer(Base):
    __tablename__ = "artifact_containers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    community_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    delegated_from_artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="Root")
    mission: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    pending_parent_proposal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    committed_content: Mapped[str] = mapped_column(Text, nullable=True)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_artifact_containers_community", "community_id", "status"),
        Index("idx_artifact_containers_delegated", "delegated_from_artifact_id"),
        Index("idx_artifact_containers_pending", "pending_parent_proposal_id"),
    )
