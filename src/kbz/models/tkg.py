"""Temporal Knowledge Graph — typed nodes, bitemporal edges, pgvector embeddings.

See /Users/uriee/.claude/plans/snazzy-crunching-abelson.md for the design rationale.

Relations are kept as free-form TEXT rather than a Python Enum because the set will
grow organically as new domain events appear (e.g. AFFECTED_BY for pulse effects).
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from kbz.models.base import Base


# ----- canonical relation names (non-exhaustive) -------------------------
class TKGRelation:
    SUPPORTED = "SUPPORTED"
    AUTHORED = "AUTHORED"
    MEMBER_OF = "MEMBER_OF"
    DELEGATED_FROM = "DELEGATED_FROM"
    COMMENTED_ON = "COMMENTED_ON"
    ALLIED_WITH = "ALLIED_WITH"
    VOTED_AGAINST = "VOTED_AGAINST"
    REFLECTED_ON = "REFLECTED_ON"
    AFFECTED_BY = "AFFECTED_BY"


class TKGNodeKind:
    USER = "user"
    COMMUNITY = "community"
    PROPOSAL = "proposal"
    PULSE = "pulse"
    ARTIFACT = "artifact"
    CONTAINER = "container"
    ACTION = "action"
    STATEMENT = "statement"
    EVENT = "event"  # synthetic nodes (e.g. a reflection summary)


# ----- tables ------------------------------------------------------------


class TKGNode(Base):
    """A typed node in the temporal knowledge graph.

    IDs are deliberately *soft* references to domain tables: when a node
    represents a User/Proposal/Artifact/etc we reuse that entity's UUID as the
    primary key, which lets the ingestor idempotently upsert on the domain ID
    without a lookup. Synthetic nodes (kind='event', reflection summaries,
    etc.) get a fresh uuid4.
    """

    __tablename__ = "tkg_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    community_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    attrs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    first_seen_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    __table_args__ = (
        Index("idx_tkg_nodes_kind_community", "kind", "community_id"),
        Index("idx_tkg_nodes_community_last_seen", "community_id", "last_seen_round"),
    )


class TKGEdge(Base):
    """A bitemporal edge between two nodes.

    valid_from_round / valid_to_round are the *logical* simulation clock; the
    created_at / ended_at timestamps are wall-clock audit data. Open edges have
    valid_to_round = NULL. Partial index on open edges accelerates the common
    "who are my current allies?" query.
    """

    __tablename__ = "tkg_edges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    src_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dst_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    community_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )
    valid_from_round: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_to_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    attrs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )

    __table_args__ = (
        Index("idx_tkg_edges_src_rel_from", "src_id", "relation", "valid_from_round"),
        Index("idx_tkg_edges_dst_rel_from", "dst_id", "relation", "valid_from_round"),
        Index("idx_tkg_edges_community_from", "community_id", "valid_from_round"),
        Index(
            "idx_tkg_edges_open",
            "src_id",
            "relation",
            postgresql_where=text("valid_to_round IS NULL"),
        ),
    )


class TKGEmbedding(Base):
    """Semantic vector anchored to a TKG node.

    Hard-FK'd to tkg_nodes: embeddings without anchors are garbage. ivfflat
    cosine index is created in the Alembic migration (SQLAlchemy can't express
    `USING ivfflat (embedding vector_cosine_ops)` natively).
    """

    __tablename__ = "tkg_embeddings"

    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tkg_nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    model: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'nomic-embed-text'"),
    )
    round_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("NOW()"),
    )
