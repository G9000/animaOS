"""create proactivity config table

Revision ID: 20260522_0001
Revises: 20260517_0001
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260522_0001"
down_revision = "20260517_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proactivity_configs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "main_chat_enabled",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "home_greeting_context_enabled",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "task_nudges_enabled",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "memory_nudges_enabled",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "checkin_nudges_enabled",
            sa.Boolean(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("custom_instruction", sa.String(length=500), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_proactivity_configs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proactivity_configs")),
        sa.UniqueConstraint("user_id", name="uq_proactivity_configs_user_id"),
    )
    op.create_index(
        op.f("ix_proactivity_configs_user_id"),
        "proactivity_configs",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_proactivity_configs_user_id"), table_name="proactivity_configs")
    op.drop_table("proactivity_configs")
