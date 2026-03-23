"""Add source column to agent_messages

Revision ID: 20260323_source
Revises: 20260319_0007
Create Date: 2026-03-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260323_source"
down_revision: str | None = "20260319_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_messages") as batch_op:
        batch_op.drop_column("source")
