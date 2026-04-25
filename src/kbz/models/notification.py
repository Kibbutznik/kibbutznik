"""Per-user durable notification inbox.

Solves the "what's new since I last looked" gap: `event_bus` already
fans out signals to anyone with a live `/ws/events` socket, but a
member who closes their laptop loses the firehose. A row in this
table is the persistent record they read on next visit.

Each row is a fan-out target — one event becomes N notifications,
one per recipient member. We pre-compute recipients at emit time
rather than re-deriving them on read so the dashboard query is a
plain `WHERE user_id = ? ORDER BY created_at DESC`.

`payload_json` carries the event-specific extras (proposal_id,
proposal_type, proposal_text snippet, etc.) so the dashboard
renders without a JOIN cascade. Cap on payload size lives in the
service layer.

Pruning is the caller's responsibility (cron / future scheduled
job). We don't TTL-delete here because "I want to scroll back"
behavior matters for governance audits.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


# A small allow-list of notification kinds. Keeping these as strings
# (not an enum) because adding a new kind shouldn't require a model
# change — the dashboard renders by `kind`, not by enum value.
KIND_PROPOSAL_CREATED = "proposal.created"
KIND_PROPOSAL_ACCEPTED = "proposal.accepted"
KIND_PROPOSAL_REJECTED = "proposal.rejected"
KIND_PROPOSAL_CANCELED = "proposal.canceled"
KIND_COMMENT_POSTED = "comment.posted"


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    # Optional community scope so a viewer can filter "what's new in
    # kibbutz X" vs "everything".
    community_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # Free-form per-kind payload — proposal_id, proposal_type,
    # truncated text, etc. The dashboard renders off these fields
    # so the read path doesn't need to JOIN.
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )
    # NULL = unread. Set to NOW() when the user marks it read. We
    # keep a timestamp rather than a boolean so a future "what
    # changed since I last opened the inbox" query is just
    # `read_at >= last_visit_at`.
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        # The hot path is "list my unread notifications, newest first".
        Index(
            "idx_notifications_user_unread",
            "user_id", "read_at", "created_at",
        ),
        # Secondary lookup: scope to a community.
        Index("idx_notifications_user_community", "user_id", "community_id"),
    )
