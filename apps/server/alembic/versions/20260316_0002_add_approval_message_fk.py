"""add pending_approval_message_id to agent_runs

Revision ID: 20260316_0002
Revises: 20260316_0001
Create Date: 2026-03-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260316_0002"
down_revision = "20260316_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "pending_approval_message_id",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        op.f("fk_agent_runs_pending_approval_message_id_agent_messages"),
        "agent_runs",
        "agent_messages",
        ["pending_approval_message_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_agent_runs_pending_approval_message_id_agent_messages"),
        "agent_runs",
        type_="foreignkey",
    )
    op.drop_column("agent_runs", "pending_approval_message_id")
