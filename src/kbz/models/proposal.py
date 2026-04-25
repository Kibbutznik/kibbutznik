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
    # The proposer's "why": a short rationale explaining why this should be
    # accepted. Separate from proposal_text (the *what*). Nullable for legacy
    # rows; new proposals are expected to include one.
    pitch: Mapped[str | None] = mapped_column(Text, nullable=True)
    val_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    val_text: Mapped[str] = mapped_column(Text, nullable=True, default="")
    # For EditArtifact proposals: snapshot of the artifact's content at the
    # moment the proposal was created. Lets the viewer (and historians) see
    # exactly what was being replaced even after the artifact has moved on.
    prev_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    pulse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    age: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    support_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Decision moment. NULL while DRAFT/OUT_THERE/ON_THE_AIR. Set to
    # NOW() when the proposal flips to ACCEPTED / REJECTED / CANCELED.
    # Lets the audit log answer "when was this rule passed?" without
    # walking pulse history. Distinct from created_at (when the
    # proposal was filed) — they differ by however many pulses the
    # proposal lived through.
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_proposals_community_status", "community_id", "proposal_status"),
        Index("idx_proposals_type", "community_id", "proposal_type"),
        Index("idx_proposals_pulse", "pulse_id"),
    )
