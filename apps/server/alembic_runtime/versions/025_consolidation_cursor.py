"""Dedicated consolidation restart cursor table.

Replaces scanning + mutating RuntimeBackgroundTaskRun.result_json for the
per-(user, thread) consolidation cursor, so the cursor survives task-run
pruning and lookups are indexed instead of a full-table scan.

Revision ID: 025_consolidation_cursor
Revises: 024_embedding_config
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "025_consolidation_cursor"
down_revision = "024_embedding_config"
branch_labels = None
depends_on = None

TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.TIMESTAMP(timezone=True), "sqlite"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("runtime_consolidation_cursors"):
        return

    op.create_table(
        "runtime_consolidation_cursors",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("last_processed_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "messages_processed",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runtime_consolidation_cursors_user_id",
        "runtime_consolidation_cursors",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_consolidation_cursor_scope",
        "runtime_consolidation_cursors",
        ["user_id", "thread_id"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("runtime_consolidation_cursors"):
        return
    existing = {ix["name"] for ix in inspector.get_indexes("runtime_consolidation_cursors")}
    if "ix_runtime_consolidation_cursor_scope" in existing:
        op.drop_index(
            "ix_runtime_consolidation_cursor_scope",
            table_name="runtime_consolidation_cursors",
        )
    if "ix_runtime_consolidation_cursors_user_id" in existing:
        op.drop_index(
            "ix_runtime_consolidation_cursors_user_id",
            table_name="runtime_consolidation_cursors",
        )
    op.drop_table("runtime_consolidation_cursors")
