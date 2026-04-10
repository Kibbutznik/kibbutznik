"""Add mission column to artifact_containers

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-09 13:00:00.000000

Adds a nullable TEXT `mission` column to `artifact_containers` so each
container can carry a concrete briefing describing what kind of content
belongs inside it. This is surfaced to agents in their state dump so they
stop treating CreateArtifact like a rephrased AddStatement.

Also backfills any existing Root container with the default Kibbutznik
Handbook briefing, so the currently-running sim immediately benefits.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEFAULT_HANDBOOK_MISSION = (
    "This community is writing a Kibbutznik Handbook: a concrete, practical "
    "document a newcomer can read to understand how this community actually "
    "works — what its values look like in practice, how decisions get made, "
    "and what daily life looks like. Each artifact is ONE SECTION of that "
    "handbook, e.g. \"How we resolve disagreements\", \"The morning stand-up "
    "ritual\", \"What happens when someone wants to leave\", \"How we onboard "
    "a new member\". Sections must be specific, procedural, and written for a "
    "real reader to follow. Do NOT write mission statements, slogans, or "
    "abstract principles — those belong in Community Rules (AddStatement)."
)


def upgrade() -> None:
    op.add_column(
        "artifact_containers",
        sa.Column("mission", sa.Text(), nullable=True),
    )
    # Backfill existing Root containers (the currently-running sim has one).
    # Only touch rows where mission IS NULL and delegated_from_artifact_id IS NULL
    # (i.e. a root container, not a child spawned by DelegateArtifact).
    op.execute(
        sa.text(
            "UPDATE artifact_containers "
            "SET mission = :mission "
            "WHERE mission IS NULL AND delegated_from_artifact_id IS NULL"
        ).bindparams(mission=DEFAULT_HANDBOOK_MISSION)
    )


def downgrade() -> None:
    op.drop_column("artifact_containers", "mission")
