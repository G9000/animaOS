"""add pending_approval_message_id to agent_runs

Revision ID: 20260316_0002
Revises: 20260316_0001
Create Date: 2026-03-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260316_0002"
down_revision = "20260316_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "pending_approval_message_id",
                sa.Integer(),
                nullable=True,
            ),
        )
        batch_op.create_foreign_key(
            op.f("fk_agent_runs_pending_approval_message_id_agent_messages"),
            "agent_messages",
            ["pending_approval_message_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_constraint(
            op.f("fk_agent_runs_pending_approval_message_id_agent_messages"),
            type_="foreignkey",
        )
        batch_op.drop_column("pending_approval_message_id")
