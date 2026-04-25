"""Moderation reports — flag a comment / proposal / user as abusive.

Closes the "first harasser arrives uninvited" gap. The platform
already has ThrowOut for full removal but nothing in between
("this comment is spam, please hide it"). A Report is a
lightweight signal a community can act on without burning a full
proposal cycle.

Lifecycle:

  OPEN     — just filed
  UPHELD   — a community member confirmed it (action implied:
             hide, mute, escalate to ThrowOut)
  DISMISSED — false alarm

Resolution semantics are deliberately community-internal: any
active member of the same community can move OPEN → UPHELD or
OPEN → DISMISSED. Heavier moderation ladders (admin role, vote
threshold, bot-resistance) are a follow-up.

Reports do NOT delete or hide their target by themselves — they
flag it for human review. Auto-hide thresholds are out of scope
for this cycle.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


# Target kinds. String not enum for the same "adding a kind shouldn't
# require a migration" reason as elsewhere in this codebase.
TARGET_COMMENT = "comment"
TARGET_PROPOSAL = "proposal"
TARGET_REASON = "reason"
TARGET_USER = "user"
TARGET_KINDS = (TARGET_COMMENT, TARGET_PROPOSAL, TARGET_REASON, TARGET_USER)

# Lifecycle states. Integers (not strings) so a future ladder
# (queued / under_review / upheld_severe) sorts cleanly.
STATUS_OPEN = 1
STATUS_UPHELD = 2
STATUS_DISMISSED = 3
STATUS_TO_NAME = {
    STATUS_OPEN: "open",
    STATUS_UPHELD: "upheld",
    STATUS_DISMISSED: "dismissed",
}


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    # Where this report belongs. Reports are scoped to a community
    # so members can triage without seeing every other kibbutz's
    # mess. For target_kind="user" the community is "where they
    # caused trouble".
    community_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    reporter_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[int] = mapped_column(
        Integer, nullable=False, default=STATUS_OPEN, server_default="1",
    )
    # Who closed the report (set when status leaves OPEN). NULL
    # while OPEN.
    resolver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    __table_args__ = (
        # Hot path: "what's open in my community"
        Index("idx_reports_community_status", "community_id", "status"),
        # Lookup the reports against a particular target
        Index("idx_reports_target", "target_kind", "target_id"),
    )
