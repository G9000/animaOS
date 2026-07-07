"""Retry caps for pending memory ops and thread-archival backoff state.

Revision ID: 022_retry_hygiene
Revises: 021_repair_profile_update_candidates
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "022_retry_hygiene"
down_revision = "021_repair_profile_update_candidates"
branch_labels = None
depends_on = None

TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.TIMESTAMP(timezone=True), "sqlite"
)


def upgrade() -> None:
    with op.batch_alter_table("pending_memory_ops") as batch_op:
        batch_op.add_column(
            sa.Column(
                "retry_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )

    with op.batch_alter_table("runtime_threads") as batch_op:
        batch_op.add_column(
            sa.Column(
                "archive_retry_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column("archive_next_retry_at", TIMESTAMPTZ, nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "archive_failed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("runtime_threads") as batch_op:
        batch_op.drop_column("archive_failed")
        batch_op.drop_column("archive_next_retry_at")
        batch_op.drop_column("archive_retry_count")

    with op.batch_alter_table("pending_memory_ops") as batch_op:
        batch_op.drop_column("retry_count")
