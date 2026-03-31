"""Add tags_json column to memory_candidates.

Revision ID: 010_candidate_tags
Revises: 009_multi_thread
Create Date: 2026-04-01
"""
from alembic import op
import sqlalchemy as sa

revision = "010_candidate_tags"
down_revision = "009_multi_thread"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_candidates",
        sa.Column("tags_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("memory_candidates", "tags_json")
