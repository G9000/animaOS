"""Add salience metadata to runtime memory candidates.

Revision ID: 019_candidate_salience
Revises: 018_profile_update_candidates
Create Date: 2026-07-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "019_candidate_salience"
down_revision = "018_profile_update_candidates"
branch_labels = None
depends_on = None

JSON = postgresql.JSON(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    with op.batch_alter_table("memory_candidates") as batch_op:
        batch_op.add_column(sa.Column("salience_json", JSON, nullable=True))

    op.drop_index("uq_memory_candidates_active_hash", table_name="memory_candidates")
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_candidates_active_hash "
        "ON memory_candidates(content_hash) "
        "WHERE status NOT IN ('rejected', 'reinforced', 'superseded', 'failed')"
    )


def downgrade() -> None:
    op.drop_index("uq_memory_candidates_active_hash", table_name="memory_candidates")
    op.execute(
        "CREATE UNIQUE INDEX uq_memory_candidates_active_hash "
        "ON memory_candidates(content_hash) "
        "WHERE status NOT IN ('rejected', 'superseded', 'failed')"
    )

    with op.batch_alter_table("memory_candidates") as batch_op:
        batch_op.drop_column("salience_json")
