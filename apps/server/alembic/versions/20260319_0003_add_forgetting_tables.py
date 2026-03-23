"""Add intentional forgetting tables and columns.

Revision ID: 20260319_0003
Revises: 20260319_0002
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260319_0003"
down_revision = "20260319_0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "forget_audit_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "forgotten_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("items_forgotten", sa.Integer, nullable=False, server_default="0"),
        sa.Column("derived_refs_affected", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_forget_audit_log_user_id", "forget_audit_log", ["user_id"])

    op.add_column(
        "memory_episodes",
        sa.Column(
            "needs_regeneration",
            sa.Boolean,
            nullable=False,
            server_default="0",
        ),
    )

    op.add_column(
        "self_model_blocks",
        sa.Column(
            "needs_regeneration",
            sa.Boolean,
            nullable=False,
            server_default="0",
        ),
    )


def downgrade():
    op.drop_column("self_model_blocks", "needs_regeneration")
    op.drop_column("memory_episodes", "needs_regeneration")
    op.drop_table("forget_audit_log")
