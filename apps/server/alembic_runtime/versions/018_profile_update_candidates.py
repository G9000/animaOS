"""Add profile update candidates.

Revision ID: 018_profile_update_candidates
Revises: 017_document_tables
Create Date: 2026-06-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "018_profile_update_candidates"
down_revision = "017_document_tables"
branch_labels = None
depends_on = None

SOURCE_MESSAGE_IDS = postgresql.ARRAY(sa.Integer()).with_variant(sa.JSON(), "sqlite")
TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.DateTime(timezone=True),
    "sqlite",
)


def upgrade() -> None:
    op.create_table(
        "profile_update_candidates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "confidence",
            sa.Float(),
            server_default=sa.text("0.8"),
            nullable=False,
        ),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.String(length=32),
            server_default=sa.text("'llm'"),
            nullable=False,
        ),
        sa.Column("source_message_ids", SOURCE_MESSAGE_IDS, nullable=True),
        sa.Column("extraction_model", sa.String(length=128), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'extracted'"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "retry_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_profile_update_candidates_user_status",
        "profile_update_candidates",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_profile_update_candidates_hash",
        "profile_update_candidates",
        ["content_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_profile_update_candidates_hash",
        table_name="profile_update_candidates",
    )
    op.drop_index(
        "ix_profile_update_candidates_user_status",
        table_name="profile_update_candidates",
    )
    op.drop_table("profile_update_candidates")
