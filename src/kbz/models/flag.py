"""Symmetric flags — community signal as +1 / -1 marks on content.

Replaces the abandoned "moderation reports" framing (PR #17) with a
single feature that doubles as a closeness signal:

- A member can flag any comment / proposal / reason / user with
  `value = +1` (positive) or `value = -1` (negative).
- One flag per (flagger, target) — re-flagging with a new value
  REPLACES the prior flag and re-applies the closeness delta in
  the new direction.
- Flagging is symmetric and visible to every active member of the
  community (no moderator role — kibbutznik has no roles).

Side effect: each flag bumps the closeness score between the
flagger and the target's *author*. A positive flag is a
small "we're closer" nudge; a negative one nudges the other
direction. Removing or flipping a flag reverses the prior delta
before applying the new one so totals stay consistent.

Targets:
- "comment"  → comments.id (author = comments.user_id)
- "proposal" → proposals.id (author = proposals.user_id)
- "reason"   → reasons.id (author = reasons.user_id)
- "user"     → users.id (author == target_id; flagging a person directly)
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


# Allowed target kinds. Bare strings (not Enum) for the same reason
# as the rest of the codebase: dashboards render by string.
TARGET_COMMENT = "comment"
TARGET_PROPOSAL = "proposal"
TARGET_REASON = "reason"
TARGET_USER = "user"
TARGET_KINDS = (TARGET_COMMENT, TARGET_PROPOSAL, TARGET_REASON, TARGET_USER)

# Allowed values. We deliberately do NOT support 0 — clearing a flag
# is a separate DELETE call so the audit log of "who has expressed an
# opinion here" stays meaningful.
VALUE_POSITIVE = 1
VALUE_NEGATIVE = -1
VALUES = (VALUE_POSITIVE, VALUE_NEGATIVE)


class Flag(Base):
    __tablename__ = "flags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    flagger_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    # Membership scope — the flagger has to be an active member of
    # this community. We store it (rather than re-resolve) because
    # comments + reasons can travel across communities via delegation
    # and we want the membership gate to anchor on the community the
    # flagger is *acting in*, not whatever community the target
    # currently belongs to.
    community_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    __table_args__ = (
        # One flag per user per target. Re-flagging is REPLACE + re-
        # apply closeness delta — see FlagService.set_flag.
        UniqueConstraint(
            "flagger_user_id", "target_kind", "target_id",
            name="uq_flags_flagger_target",
        ),
        # Hot path: aggregate counts for "what does the community
        # think of THIS comment".
        Index("idx_flags_target", "target_kind", "target_id"),
        # "What have I flagged in this community" listing for the
        # flagger's own UI.
        Index("idx_flags_flagger_community", "flagger_user_id", "community_id"),
        CheckConstraint("value IN (-1, 1)", name="ck_flags_value"),
    )
