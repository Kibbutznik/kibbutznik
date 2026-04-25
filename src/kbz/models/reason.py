"""Structured deliberation threads under a proposal.

Closes the "argue substantively before voting" gap. Comment is
chit-chat; Reason is a stance-marked claim ("PRO: …" / "CON: …")
that members can reply to with counter-claims, forming a tree.

Why a separate table from Comment:

- Stance is first-class. The dashboard renders the pro and con
  columns next to each other rather than mixing them in time-
  order with random small talk.
- It's `proposal_id`-scoped and only `proposal_id`-scoped — not
  the polymorphic `entity_type` shape that Comment carries. The
  query path is hot ("show me the deliberation under proposal X")
  and a single FK is the cheapest index.
- Soft-delete is meaningful for governance audits: a removed
  reason still leaves its child counter-reasons readable. We use
  a STATUS column rather than a hard DELETE.

Future extensions (not in this cycle): per-reason score voting,
"I changed my mind" markers (a stance flip annotation), reason
quality flags.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime, ForeignKey, Index, Integer, String, Text, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


# Stance allow-list. Kept as bare strings (not Enum) because the
# dashboard renders by string. Adding "neutral" later won't require
# a column type change.
STANCE_PRO = "pro"
STANCE_CON = "con"
STANCES = (STANCE_PRO, STANCE_CON)

STATUS_ACTIVE = 1
STATUS_REMOVED = 2


class Reason(Base):
    __tablename__ = "reasons"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    # "pro" or "con". On a counter-reply (parent_reason_id set) the
    # stance is OPPOSITE to the parent: a counter to a PRO is a CON.
    # We don't enforce that here — the service layer does — so test
    # data can still construct edge cases.
    stance: Mapped[str] = mapped_column(String(8), nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    parent_reason_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reasons.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[int] = mapped_column(
        Integer, nullable=False, default=STATUS_ACTIVE, server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    __table_args__ = (
        # Hot path: fetch all reasons for a proposal.
        Index("idx_reasons_proposal", "proposal_id"),
        # Tree walk: find children of a parent.
        Index("idx_reasons_parent", "parent_reason_id"),
    )
