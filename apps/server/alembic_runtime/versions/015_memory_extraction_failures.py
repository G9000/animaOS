"""Add memory extraction failure queue.

Revision ID: 015_memory_extraction_failures
Revises: 014_embedding_checksums
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "015_memory_extraction_failures"
down_revision = "014_embedding_checksums"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_extraction_failures",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_message_ids",
            postgresql.JSON(astext_type=sa.Text()),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column("user_message_preview", sa.Text(), nullable=True),
        sa.Column("assistant_response_preview", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=False),
        sa.Column("extraction_model", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'failed'"),
            nullable=False,
        ),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_attempt_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_extraction_failures_user_status",
        "memory_extraction_failures",
        ["user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memory_extraction_failures_user_status",
        table_name="memory_extraction_failures",
    )
    op.drop_table("memory_extraction_failures")
