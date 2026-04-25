"""Per-user comment votes.

Backs the up/down arrows on comments. One row per (user, comment) so
a single user can have at most one vote per comment — pre-fix the
score endpoint blindly added the delta to comments.score, so a single
user pressing the up arrow 20 times added 20 points.

Behavior at the API layer (see CommentService.cast_vote):
- New click in either direction → INSERT.
- Click matching existing direction → DELETE (toggle off).
- Click opposite direction → UPDATE value (flip).

The cached score on Comment stays in sync via the same SQL transaction
so existing reads of Comment.score remain authoritative.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Integer, PrimaryKeyConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


VALUE_UP = 1
VALUE_DOWN = -1


class CommentVote(Base):
    __tablename__ = "comment_votes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    # ON DELETE CASCADE — if a comment is ever hard-deleted, its votes
    # go with it. Comments are soft-deleted today (no DELETE path), but
    # this keeps the FK honest if a janitor migration ever lands.
    comment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=False,
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    __table_args__ = (
        # One vote per (user, comment). Upsert on this PK to switch a
        # +1 to a -1 cleanly; DELETE on this PK to toggle off.
        PrimaryKeyConstraint(
            "user_id", "comment_id", name="pk_comment_votes",
        ),
        CheckConstraint("value IN (-1, 1)", name="ck_comment_votes_value"),
    )
