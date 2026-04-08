"""Add run grouping to runtime retrieval feedback.

Revision ID: 012_retrieval_feedback_runs
Revises: 011_retrieval_feedback
Create Date: 2026-04-08
"""

import sqlalchemy as sa
from alembic import op

revision = "012_retrieval_feedback_runs"
down_revision = "011_retrieval_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_retrieval_feedback",
        sa.Column("run_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_memory_retrieval_feedback_user_run",
        "memory_retrieval_feedback",
        ["user_id", "run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memory_retrieval_feedback_user_run",
        table_name="memory_retrieval_feedback",
    )
    op.drop_column("memory_retrieval_feedback", "run_id")