"""add agent birthday override to agent_profile

Revision ID: 20260626_0001
Revises: 20260605_0001
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260626_0001"
down_revision = "20260605_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_profile") as batch_op:
        batch_op.add_column(
            sa.Column("agent_birthday", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_profile") as batch_op:
        batch_op.drop_column("agent_birthday")
