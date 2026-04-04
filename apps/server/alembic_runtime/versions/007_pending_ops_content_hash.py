"""Add content_hash to pending_memory_ops for idempotent replay.

Revision ID: 007_pending_ops_hash
Revises: 006_soul_writer
Create Date: 2026-03-30
"""
import sqlalchemy as sa
from alembic import op

revision = "007_pending_ops_hash"
down_revision = "006_soul_writer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pending_memory_ops",
        sa.Column("content_hash", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pending_memory_ops", "content_hash")
