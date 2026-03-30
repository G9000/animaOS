"""Multi-thread support: drop single-active-thread constraint, add is_archived_history."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "009_multi_thread"
down_revision = "008_session_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the constraint that only allowed one active thread per user.
    op.drop_index("uq_runtime_threads_active_user", table_name="runtime_threads")

    # Add is_archived_history to runtime_messages.
    # Messages rehydrated from JSONL archive are flagged here so the agent
    # context loader can skip them (while the UI still shows them).
    op.add_column(
        "runtime_messages",
        sa.Column(
            "is_archived_history",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_runtime_messages_thread_archived_history",
        "runtime_messages",
        ["thread_id", "is_archived_history"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_messages_thread_archived_history",
        table_name="runtime_messages",
    )
    op.drop_column("runtime_messages", "is_archived_history")
    op.create_index(
        "uq_runtime_threads_active_user",
        "runtime_threads",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
