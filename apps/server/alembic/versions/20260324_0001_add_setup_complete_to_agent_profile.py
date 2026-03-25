"""add setup_complete to agent_profile

Revision ID: 20260324_0001
Revises: 20260323_source
Create Date: 2026-03-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260324_0001"
down_revision = "20260323_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_profile") as batch_op:
        batch_op.add_column(
            sa.Column("setup_complete", sa.Boolean(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_profile") as batch_op:
        batch_op.drop_column("setup_complete")
