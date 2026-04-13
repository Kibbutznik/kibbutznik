import uuid
from datetime import datetime

from sqlalchemy import Float, Index, Integer, String, Text, DateTime, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


class AgentMemory(Base):
    """Persistent memory for AI agents.

    Stores episodic events, goals, relationships, and reflections so that
    agents can learn from past experience and maintain continuity across
    simulation rounds.
    """

    __tablename__ = "agent_memories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
    )
    memory_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )  # "episodic" | "goal" | "relationship" | "reflection"
    category: Mapped[str] = mapped_column(
        String(50), nullable=True, default=None,
    )  # e.g. "proposal_outcome", "social", "artifact_work"
    content: Mapped[str] = mapped_column(
        Text, nullable=False,
    )
    importance: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5,
    )  # 0.0-1.0, for ranking/pruning
    round_num: Mapped[int] = mapped_column(
        Integer, nullable=True, default=None,
    )  # simulation round when created
    related_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=True, default=None,
    )  # proposal_id, user_id, community_id, etc.
    expires_at: Mapped[int] = mapped_column(
        Integer, nullable=True, default=None,
    )  # round number after which to prune (NULL = never)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    __table_args__ = (
        Index("idx_agent_memories_user_type", "user_id", "memory_type"),
        Index("idx_agent_memories_user_importance", "user_id", "importance"),
        Index("idx_agent_memories_user_round", "user_id", "round_num"),
    )
