"""Create runtime tables

Revision ID: 001
Revises:
Create Date: 2026-03-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)

# revision identifiers, used by Alembic.
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create all runtime tables."""
    # --- runtime_threads ---
    op.create_table(
        "runtime_threads",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("last_message_at", TIMESTAMPTZ, nullable=True),
        sa.Column("next_message_sequence", sa.Integer, nullable=False, server_default=sa.text("1")),
    )
    op.create_index("ix_runtime_threads_user_id", "runtime_threads", ["user_id"], unique=True)

    # --- runtime_runs ---
    op.create_table(
        "runtime_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "thread_id",
            sa.BigInteger,
            sa.ForeignKey("runtime_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="running"),
        sa.Column("stop_reason", sa.String(64), nullable=True),
        sa.Column("error_text", sa.Text, nullable=True),
        sa.Column("started_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", TIMESTAMPTZ, nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("total_tokens", sa.Integer, nullable=True),
        sa.Column("pending_approval_message_id", sa.BigInteger, nullable=True),
    )
    op.create_index("ix_runtime_runs_user_id", "runtime_runs", ["user_id"])
    op.create_index(
        "ix_runtime_runs_pending_approval_message_id",
        "runtime_runs",
        ["pending_approval_message_id"],
    )

    # --- runtime_steps ---
    op.create_table(
        "runtime_steps",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "run_id",
            sa.BigInteger,
            sa.ForeignKey("runtime_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            sa.BigInteger,
            sa.ForeignKey("runtime_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("request_json", JSON, nullable=False),
        sa.Column("response_json", JSON, nullable=False),
        sa.Column("tool_calls_json", JSON, nullable=True),
        sa.Column("usage_json", JSON, nullable=True),
        sa.Column("error_text", sa.Text, nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "step_index", name="uq_runtime_steps_run_id_step_index"),
    )
    op.create_index("ix_runtime_steps_thread_id", "runtime_steps", ["thread_id"])

    # --- runtime_messages ---
    op.create_table(
        "runtime_messages",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "thread_id",
            sa.BigInteger,
            sa.ForeignKey("runtime_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column(
            "run_id",
            sa.BigInteger,
            sa.ForeignKey("runtime_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "step_id",
            sa.BigInteger,
            sa.ForeignKey("runtime_steps.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sequence_id", sa.Integer, nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("content_text", sa.Text, nullable=True),
        sa.Column("content_json", JSON, nullable=True),
        sa.Column("tool_name", sa.String(128), nullable=True),
        sa.Column("tool_call_id", sa.String(128), nullable=True),
        sa.Column("tool_args_json", JSON, nullable=True),
        sa.Column("is_in_context", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("token_estimate", sa.Integer, nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "thread_id", "sequence_id", name="uq_runtime_messages_thread_id_sequence_id"
        ),
    )
    op.create_index("ix_runtime_messages_user_id", "runtime_messages", ["user_id"])
    op.create_index("ix_runtime_messages_run_id", "runtime_messages", ["run_id"])
    op.create_index(
        "ix_runtime_messages_user_created",
        "runtime_messages",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_runtime_messages_thread_context",
        "runtime_messages",
        ["thread_id", "is_in_context"],
    )

    # --- runtime_background_task_runs ---
    op.create_table(
        "runtime_background_task_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("result_json", JSON, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", TIMESTAMPTZ, nullable=True),
        sa.Column("completed_at", TIMESTAMPTZ, nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_runtime_background_task_runs_user_id",
        "runtime_background_task_runs",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_bg_task_runs_user_status",
        "runtime_background_task_runs",
        ["user_id", "status"],
    )


def downgrade() -> None:
    """Drop all runtime tables in reverse order."""
    op.drop_table("runtime_background_task_runs")
    op.drop_table("runtime_messages")
    op.drop_table("runtime_steps")
    op.drop_table("runtime_runs")
    op.drop_table("runtime_threads")
