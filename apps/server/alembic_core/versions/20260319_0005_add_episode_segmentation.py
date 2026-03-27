"""Add episode segmentation columns for batch topic grouping.

Revision ID: 20260319_0004
Revises: 20260319_0003
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260319_0005"
down_revision = "20260319_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_episodes",
        sa.Column("message_indices_json", sa.JSON, nullable=True),
    )
    op.add_column(
        "memory_episodes",
        sa.Column(
            "segmentation_method",
            sa.String(20),
            nullable=False,
            server_default="sequential",
        ),
    )


def downgrade() -> None:
    op.drop_column("memory_episodes", "segmentation_method")
    op.drop_column("memory_episodes", "message_indices_json")
