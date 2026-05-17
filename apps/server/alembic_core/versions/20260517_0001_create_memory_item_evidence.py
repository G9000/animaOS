"""create memory item evidence table

Revision ID: 20260517_0001
Revises: 20260408_0001
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260517_0001"
down_revision = "20260408_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_item_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("memory_item_id", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("runtime_thread_id", sa.Integer(), nullable=True),
        sa.Column("runtime_message_id", sa.Integer(), nullable=True),
        sa.Column("runtime_message_ids_json", sa.JSON(), nullable=True),
        sa.Column("transcript_ref", sa.String(length=255), nullable=True),
        sa.Column("sequence_id", sa.Integer(), nullable=True),
        sa.Column("speaker", sa.String(length=24), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "confidence",
            sa.Float(),
            server_default=sa.text("1.0"),
            nullable=False,
        ),
        sa.Column("extractor", sa.String(length=128), nullable=True),
        sa.Column("evidence_text", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_memory_item_evidence_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["memory_item_id"],
            ["memory_items.id"],
            name=op.f("fk_memory_item_evidence_memory_item_id_memory_items"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_memory_item_evidence_user_item",
        "memory_item_evidence",
        ["user_id", "memory_item_id"],
    )
    op.create_index(
        "ix_memory_item_evidence_user_observed",
        "memory_item_evidence",
        ["user_id", "observed_at"],
    )
    op.create_index(
        "ix_memory_item_evidence_source_observed",
        "memory_item_evidence",
        ["user_id", "source_kind", "observed_at"],
    )
    op.create_index(
        "ix_memory_item_evidence_runtime_message",
        "memory_item_evidence",
        ["runtime_message_id"],
    )
    op.create_index(
        "ix_memory_item_evidence_transcript_ref",
        "memory_item_evidence",
        ["transcript_ref"],
    )


def downgrade() -> None:
    op.drop_table("memory_item_evidence")
