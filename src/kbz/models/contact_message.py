"""Inbound "Get in touch" messages from the public site.

A visitor leaves a message via the contact form (landing/contact.html →
POST /contact). We persist FIRST and email the operator second (best
effort) so a message is never lost to an email outage. The operator
reads them either in the inbox they arrive at OR via the admin-gated
GET /admin/contact, which reads this table.

Name + email are optional (a visitor can leave a message anonymously);
message is required. ip + user_agent are captured server-side for abuse
triage, never trusted from the body.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


class ContactMessage(Base):
    __tablename__ = "contact_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Captured server-side for abuse triage. Never from the request body.
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Operator workflow flag — flip when triaged/replied. Lets the admin
    # view filter "new" without deleting history.
    handled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    __table_args__ = (
        # Hot path: admin view lists newest-first, optionally unhandled.
        Index("idx_contact_messages_created", "created_at"),
        Index("idx_contact_messages_handled_created", "handled", "created_at"),
    )
