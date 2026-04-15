"""TKG core — nodes, edges, embeddings + pgvector extension.

Revision ID: d7a8f9e0b1c2
Revises: a4b5c6d7e8f9
Create Date: 2026-04-15 12:00:00.000000

Creates the Temporal Knowledge Graph schema:
- tkg_nodes: typed entity nodes (soft FK to domain UUIDs)
- tkg_edges: bitemporal edges with partial index on open edges
- tkg_embeddings: pgvector cosine KNN anchored to nodes
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d7a8f9e0b1c2"
down_revision: Union[str, None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---- tkg_nodes ------------------------------------------------------
    op.create_table(
        "tkg_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("community_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "attrs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("first_seen_round", sa.Integer(), nullable=True),
        sa.Column("last_seen_round", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_tkg_nodes_kind_community", "tkg_nodes", ["kind", "community_id"])
    op.create_index(
        "idx_tkg_nodes_community_last_seen",
        "tkg_nodes",
        ["community_id", "last_seen_round"],
    )

    # ---- tkg_edges ------------------------------------------------------
    op.create_table(
        "tkg_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("src_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dst_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("community_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("valid_from_round", sa.Integer(), nullable=False),
        sa.Column("valid_to_round", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column(
            "attrs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "idx_tkg_edges_src_rel_from",
        "tkg_edges",
        ["src_id", "relation", "valid_from_round"],
    )
    op.create_index(
        "idx_tkg_edges_dst_rel_from",
        "tkg_edges",
        ["dst_id", "relation", "valid_from_round"],
    )
    op.create_index(
        "idx_tkg_edges_community_from",
        "tkg_edges",
        ["community_id", "valid_from_round"],
    )
    # partial index on open edges — hot path for "current neighbors"
    op.execute(
        "CREATE INDEX idx_tkg_edges_open "
        "ON tkg_edges (src_id, relation) "
        "WHERE valid_to_round IS NULL"
    )

    # ---- tkg_embeddings -------------------------------------------------
    op.execute(
        """
        CREATE TABLE tkg_embeddings (
            node_id    UUID PRIMARY KEY REFERENCES tkg_nodes(id) ON DELETE CASCADE,
            content    TEXT NOT NULL,
            embedding  VECTOR(768) NOT NULL,
            model      TEXT NOT NULL DEFAULT 'nomic-embed-text',
            round_num  INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # ivfflat cosine index — 100 lists is the recommended default for <1M rows
    op.execute(
        "CREATE INDEX idx_tkg_embeddings_ivfflat "
        "ON tkg_embeddings USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_tkg_embeddings_ivfflat")
    op.execute("DROP TABLE IF EXISTS tkg_embeddings")

    op.execute("DROP INDEX IF EXISTS idx_tkg_edges_open")
    op.drop_index("idx_tkg_edges_community_from", table_name="tkg_edges")
    op.drop_index("idx_tkg_edges_dst_rel_from", table_name="tkg_edges")
    op.drop_index("idx_tkg_edges_src_rel_from", table_name="tkg_edges")
    op.drop_table("tkg_edges")

    op.drop_index("idx_tkg_nodes_community_last_seen", table_name="tkg_nodes")
    op.drop_index("idx_tkg_nodes_kind_community", table_name="tkg_nodes")
    op.drop_table("tkg_nodes")

    # Intentionally NOT dropping the vector extension — other schemas may use it.
