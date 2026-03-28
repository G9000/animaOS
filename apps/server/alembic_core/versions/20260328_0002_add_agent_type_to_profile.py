"""add agent_type to agent_profile

Revision ID: 20260328_0002
Revises: 20260328_0001
Create Date: 2026-03-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260328_0002"
down_revision = "20260328_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_profile") as batch_op:
        batch_op.add_column(
            sa.Column(
                "agent_type",
                sa.String(32),
                nullable=False,
                server_default="companion",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_profile") as batch_op:
        batch_op.drop_column("agent_type")
