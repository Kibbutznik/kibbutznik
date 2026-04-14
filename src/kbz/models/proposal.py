import uuid
from datetime import datetime

from sqlalchemy import Index, Integer, String, Text, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    community_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proposal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    proposal_status: Mapped[str] = mapped_column(String(20), nullable=False, default="Draft")
    proposal_text: Mapped[str] = mapped_column(Text, nullable=True, default="")
    val_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    val_text: Mapped[str] = mapped_column(Text, nullable=True, default="")
    # For EditArtifact proposals: snapshot of the artifact's content at the
    # moment the proposal was created. Lets the viewer (and historians) see
    # exactly what was being replaced even after the artifact has moved on.
    prev_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    pulse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    age: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    support_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_proposals_community_status", "community_id", "proposal_status"),
        Index("idx_proposals_type", "community_id", "proposal_type"),
        Index("idx_proposals_pulse", "pulse_id"),
    )
