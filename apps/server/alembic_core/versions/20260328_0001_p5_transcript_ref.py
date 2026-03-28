"""P5: Add transcript_ref to memory_episodes.

Revision ID: 20260328_0001
Revises: 20260327_0001
Create Date: 2026-03-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260328_0001"
down_revision = "20260327_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("memory_episodes") as batch_op:
        batch_op.add_column(
            sa.Column("transcript_ref", sa.String(length=255), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("memory_episodes") as batch_op:
        batch_op.drop_column("transcript_ref")
