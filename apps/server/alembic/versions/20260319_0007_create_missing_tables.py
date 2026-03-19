"""Create memory_vectors, telegram_links, discord_links tables.

These tables existed in the SQLAlchemy models but had no Alembic migration
(previously created by Base.metadata.create_all).

Revision ID: 20260319_0007
Revises: 20260319_0006
Create Date: 2026-03-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260319_0007"
down_revision = "20260319_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_vectors",
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(24), nullable=False, server_default="fact"),
        sa.Column("importance", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("item_id"),
        sa.ForeignKeyConstraint(
            ["item_id"], ["memory_items.id"],
            name=op.f("fk_memory_vectors_item_id_memory_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_memory_vectors_user_id_users"),
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_memory_vectors_user_id", "memory_vectors", ["user_id"])

    op.create_table(
        "telegram_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_telegram_links_user_id_users"),
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "discord_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_discord_links_user_id_users"),
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("discord_links")
    op.drop_table("telegram_links")
    op.drop_index("ix_memory_vectors_user_id", table_name="memory_vectors")
    op.drop_table("memory_vectors")
