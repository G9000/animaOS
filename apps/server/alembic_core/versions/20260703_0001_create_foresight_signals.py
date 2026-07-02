"""create foresight signals

Revision ID: 20260703_0001
Revises: 20260701_0003
Create Date: 2026-07-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260703_0001"
down_revision = "20260701_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "foresight_signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("relative_text", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            server_default=sa.text("0.8"),
            nullable=False,
        ),
        sa.Column("source_thread_id", sa.Integer(), nullable=True),
        sa.Column("source_message_ids_json", sa.JSON(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_foresight_signals_user_status",
        "foresight_signals",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_foresight_signals_user_start",
        "foresight_signals",
        ["user_id", "start_date"],
    )
    op.create_index(
        "ix_foresight_signals_user_thread",
        "foresight_signals",
        ["user_id", "source_thread_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_foresight_signals_user_thread", table_name="foresight_signals")
    op.drop_index("ix_foresight_signals_user_start", table_name="foresight_signals")
    op.drop_index("ix_foresight_signals_user_status", table_name="foresight_signals")
    op.drop_table("foresight_signals")
