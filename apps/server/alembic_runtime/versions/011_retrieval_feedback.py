"""Add runtime retrieval feedback table.

Revision ID: 011_retrieval_feedback
Revises: 010_candidate_tags
Create Date: 2026-04-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "011_retrieval_feedback"
down_revision = "010_candidate_tags"
branch_labels = None
depends_on = None

TIMESTAMPTZ = TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "memory_retrieval_feedback",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("memory_item_id", sa.Integer, nullable=False),
        sa.Column("was_used", sa.Boolean, nullable=False),
        sa.Column("evidence_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("synced", sa.Boolean, nullable=False, server_default="false"),
    )
    op.create_index(
        "ix_memory_retrieval_feedback_user_item",
        "memory_retrieval_feedback",
        ["user_id", "memory_item_id"],
    )
    op.execute(
        "CREATE INDEX ix_memory_retrieval_feedback_unsynced "
        "ON memory_retrieval_feedback(user_id) WHERE synced = FALSE"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memory_retrieval_feedback_user_item",
        table_name="memory_retrieval_feedback",
    )
    op.drop_table("memory_retrieval_feedback")