"""P4: Add pending memory ops to runtime."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_memory_ops",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("op_type", sa.String(length=16), nullable=False),
        sa.Column("target_block", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("old_content", sa.Text(), nullable=True),
        sa.Column("source_run_id", sa.BigInteger(), nullable=True),
        sa.Column("source_tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column(
            "consolidated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("consolidated_at", TIMESTAMPTZ, nullable=True),
        sa.Column("failed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("failure_reason", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_pending_ops_user_pending",
        "pending_memory_ops",
        ["user_id", "consolidated", "failed"],
    )


def downgrade() -> None:
    op.drop_index("ix_pending_ops_user_pending", table_name="pending_memory_ops")
    op.drop_table("pending_memory_ops")
