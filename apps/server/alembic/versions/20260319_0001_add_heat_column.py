"""Add heat column to memory_items.

Revision ID: 20260319_0001
Revises: 20260316_0003
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260319_0001"
down_revision = "20260316_0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "memory_items",
        sa.Column("heat", sa.Float, nullable=False, server_default="0.0"),
    )
    op.create_index(
        "ix_memory_items_user_heat",
        "memory_items",
        ["user_id", "heat"],
    )


def downgrade():
    op.drop_index("ix_memory_items_user_heat")
    op.drop_column("memory_items", "heat")
