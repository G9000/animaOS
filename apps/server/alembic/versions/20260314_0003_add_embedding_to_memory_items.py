"""Add embedding_json column to memory_items table.

Revision ID: 20260314_0003
Revises: 20260314_0002
Create Date: 2026-03-14
"""

import sqlalchemy as sa
from alembic import op

revision = "20260314_0003"
down_revision = "20260314_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_items",
        sa.Column("embedding_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("memory_items", "embedding_json")
