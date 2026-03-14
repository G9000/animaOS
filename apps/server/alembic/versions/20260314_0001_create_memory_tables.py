"""create memory tables

Revision ID: 20260314_0001
Revises: 623075d8d13e
Create Date: 2026-03-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260314_0001"
down_revision = "623075d8d13e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="'extraction'"),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column("last_referenced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reference_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["superseded_by"], ["memory_items.id"], ondelete="SET NULL"
        ),
    )

    op.create_table(
        "memory_episodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("time", sa.String(length=8), nullable=True),
        sa.Column("topics_json", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("emotional_arc", sa.String(length=128), nullable=True),
        sa.Column("significance_score", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("turn_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["agent_threads.id"], ondelete="SET NULL"
        ),
    )

    op.create_table(
        "memory_daily_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("assistant_response", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("memory_daily_logs")
    op.drop_table("memory_episodes")
    op.drop_table("memory_items")
