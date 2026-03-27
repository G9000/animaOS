"""Add a persistent message sequence counter to agent threads.

Revision ID: 20260314_0006
Revises: 20260314_0005
Create Date: 2026-03-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260314_0006"
down_revision = "20260314_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_threads") as batch_op:
        batch_op.add_column(
            sa.Column(
                "next_message_sequence",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            )
        )

    op.execute(
        """
        UPDATE agent_threads
        SET next_message_sequence = COALESCE(
            (
                SELECT MAX(agent_messages.sequence_id) + 1
                FROM agent_messages
                WHERE agent_messages.thread_id = agent_threads.id
            ),
            1
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("agent_threads") as batch_op:
        batch_op.drop_column("next_message_sequence")
