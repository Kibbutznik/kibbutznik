"""Artifact / ArtifactContainer system + proposal text columns to TEXT

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-09 12:00:00.000000

Adds the artifact productive layer:
  - artifact_containers: per-community work bins, with delegation cascade lifecycle.
  - artifacts: versioned text contributions inside containers.

Also widens proposals.proposal_text and proposals.val_text from VARCHAR(2000)
to TEXT, since artifact content (which flows through proposals during voting)
can far exceed 2 KB.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Widen proposal text columns so artifact content can pass through them.
    op.alter_column(
        'proposals',
        'proposal_text',
        existing_type=sa.String(length=2000),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        'proposals',
        'val_text',
        existing_type=sa.String(length=2000),
        type_=sa.Text(),
        existing_nullable=True,
    )

    # 2. artifact_containers
    op.create_table(
        'artifact_containers',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('community_id', sa.UUID(), nullable=False),
        sa.Column('delegated_from_artifact_id', sa.UUID(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('status', sa.Integer(), nullable=False),
        sa.Column('pending_parent_proposal_id', sa.UUID(), nullable=True),
        sa.Column('committed_content', sa.Text(), nullable=True),
        sa.Column('committed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_artifact_containers_community', 'artifact_containers', ['community_id', 'status'], unique=False)
    op.create_index('idx_artifact_containers_delegated', 'artifact_containers', ['delegated_from_artifact_id'], unique=False)
    op.create_index('idx_artifact_containers_pending', 'artifact_containers', ['pending_parent_proposal_id'], unique=False)

    # 3. artifacts
    op.create_table(
        'artifacts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('container_id', sa.UUID(), nullable=False),
        sa.Column('community_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('author_user_id', sa.UUID(), nullable=False),
        sa.Column('proposal_id', sa.UUID(), nullable=False),
        sa.Column('prev_artifact_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_artifacts_container', 'artifacts', ['container_id', 'status'], unique=False)
    op.create_index('idx_artifacts_prev', 'artifacts', ['prev_artifact_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_artifacts_prev', table_name='artifacts')
    op.drop_index('idx_artifacts_container', table_name='artifacts')
    op.drop_table('artifacts')
    op.drop_index('idx_artifact_containers_pending', table_name='artifact_containers')
    op.drop_index('idx_artifact_containers_delegated', table_name='artifact_containers')
    op.drop_index('idx_artifact_containers_community', table_name='artifact_containers')
    op.drop_table('artifact_containers')

    op.alter_column(
        'proposals',
        'val_text',
        existing_type=sa.Text(),
        type_=sa.String(length=2000),
        existing_nullable=True,
    )
    op.alter_column(
        'proposals',
        'proposal_text',
        existing_type=sa.Text(),
        type_=sa.String(length=2000),
        existing_nullable=True,
    )
