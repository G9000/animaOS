"""add thinking monologue to agent_profile

Revision ID: 20260626_0002
Revises: 20260626_0001
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260626_0002"
down_revision = "20260626_0001"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if _has_column("agent_profile", "thinking_monologue_json"):
        return
    if not sa.inspect(op.get_bind()).has_table("agent_profile"):
        return
    with op.batch_alter_table("agent_profile") as batch_op:
        batch_op.add_column(
            sa.Column("thinking_monologue_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if not _has_column("agent_profile", "thinking_monologue_json"):
        return
    with op.batch_alter_table("agent_profile") as batch_op:
        batch_op.drop_column("thinking_monologue_json")
