"""Add corrected-state tracking to runtime retrieval feedback.

Revision ID: 013_retrieval_feedback_corrections
Revises: 012_retrieval_feedback_runs
Create Date: 2026-04-08
"""

import sqlalchemy as sa
from alembic import op

revision = "013_retrieval_feedback_corrections"
down_revision = "012_retrieval_feedback_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
    op.add_column(
        "memory_retrieval_feedback",
        sa.Column("was_corrected", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("memory_retrieval_feedback", "was_corrected")
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=255),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
