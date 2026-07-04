"""Repair missing profile update candidates table.

Revision ID: 021_repair_profile_update_candidates
Revises: 020_merge_image_assets_candidate_salience
Create Date: 2026-07-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "021_repair_profile_update_candidates"
down_revision = "020_merge_image_assets_candidate_salience"
branch_labels = None
depends_on = None

TABLE_NAME = "profile_update_candidates"
USER_STATUS_INDEX = "ix_profile_update_candidates_user_status"
HASH_INDEX = "ix_profile_update_candidates_hash"

SOURCE_MESSAGE_IDS = postgresql.ARRAY(sa.Integer()).with_variant(sa.JSON(), "sqlite")
TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.DateTime(timezone=True),
    "sqlite",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table(TABLE_NAME):
        op.create_table(
            TABLE_NAME,
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
        index_names: set[str] = set()
    else:
        index_names = {
            str(index["name"]) for index in inspector.get_indexes(TABLE_NAME) if index.get("name")
        }

    if USER_STATUS_INDEX not in index_names:
        op.create_index(
            USER_STATUS_INDEX,
            TABLE_NAME,
            ["user_id", "status"],
        )
    if HASH_INDEX not in index_names:
        op.create_index(
            HASH_INDEX,
            TABLE_NAME,
            ["content_hash"],
        )


def downgrade() -> None:
    # The table is owned by 018_profile_update_candidates. This repair migration
    # only fills in a missing table for bad local stamps, so downgrade is a no-op.
    pass
