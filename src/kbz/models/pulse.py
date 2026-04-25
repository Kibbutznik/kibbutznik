import uuid
from datetime import datetime

from sqlalchemy import Index, Integer, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base

# PulseStatus.NEXT — duplicated as a literal here so the model file
# doesn't import enums.py and create a cycle.
_NEXT = 0
_ACTIVE = 1


class Pulse(Base):
    __tablename__ = "pulses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    community_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    support_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_pulses_community_status", "community_id", "status"),
        # At most ONE pulse per community in NEXT or ACTIVE state.
        # Two concurrent threshold-crossing pulse-supports used to
        # both call execute_pulse, both create a new NEXT pulse,
        # and break every subsequent get_next_pulse() with
        # MultipleResultsFound. The partial unique indexes turn
        # that race into a clean IntegrityError the service can
        # catch and bail on.
        Index(
            "ix_pulses_one_next_per_community",
            "community_id",
            unique=True,
            postgresql_where=text(f"status = {_NEXT}"),
        ),
        Index(
            "ix_pulses_one_active_per_community",
            "community_id",
            unique=True,
            postgresql_where=text(f"status = {_ACTIVE}"),
        ),
    )
