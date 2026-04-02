"""add avatar_url to agent_profile

Revision ID: 20260402_0001
Revises: 20260330_0002
Create Date: 2026-04-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260402_0001"
down_revision = "20260330_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_profile") as batch_op:
        batch_op.add_column(
            sa.Column("avatar_url", sa.String(512), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_profile") as batch_op:
        batch_op.drop_column("avatar_url")
