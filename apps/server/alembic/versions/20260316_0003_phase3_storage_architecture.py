"""Phase 3: storage architecture — tags, claims, tags_json on memory_items

Revision ID: 20260316_0003
Revises: 20260316_0002
Create Date: 2026-03-16
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260316_0003"
down_revision = "20260316_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- tags_json on memory_items ---
    op.add_column(
        "memory_items",
        sa.Column("tags_json", sa.JSON(), nullable=True),
    )

    # --- memory_item_tags junction table ---
    op.create_table(
        "memory_item_tags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tag", sa.String(100), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["memory_items.id"],
            name=op.f("fk_memory_item_tags_item_id_memory_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_memory_item_tags_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "item_id", "tag", name="uq_memory_item_tags_item_tag"),
    )
    op.create_index("ix_memory_item_tags_tag", "memory_item_tags", ["tag"])
    op.create_index("ix_memory_item_tags_item_id",
                    "memory_item_tags", ["item_id"])
    op.create_index("ix_memory_item_tags_user_id",
                    "memory_item_tags", ["user_id"])

    # --- memory_claims ---
    op.create_table(
        "memory_claims",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(24),
                  nullable=False, server_default="user"),
        sa.Column("namespace", sa.String(24), nullable=False),
        sa.Column("slot", sa.String(64), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("polarity", sa.String(12), nullable=False,
                  server_default="positive"),
        sa.Column("confidence", sa.Float(), nullable=False,
                  server_default=sa.text("0.8")),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default="active"),
        sa.Column("canonical_key", sa.String(255), nullable=False),
        sa.Column("source_kind", sa.String(24),
                  nullable=False, server_default="extraction"),
        sa.Column("extractor", sa.String(32),
                  nullable=False, server_default="regex"),
        sa.Column("memory_item_id", sa.Integer(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_memory_claims_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["memory_item_id"],
            ["memory_items.id"],
            name=op.f("fk_memory_claims_memory_item_id_memory_items"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["memory_claims.id"],
            name=op.f("fk_memory_claims_superseded_by_id_memory_claims"),
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_memory_claims_user_id", "memory_claims", ["user_id"])
    op.create_index(
        "ix_memory_claims_user_canonical",
        "memory_claims",
        ["user_id", "canonical_key"],
    )

    # --- memory_claim_evidence ---
    op.create_table(
        "memory_claim_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["memory_claims.id"],
            name=op.f("fk_memory_claim_evidence_claim_id_memory_claims"),
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_memory_claim_evidence_claim_id",
                    "memory_claim_evidence", ["claim_id"])


def downgrade() -> None:
    op.drop_table("memory_claim_evidence")
    op.drop_table("memory_claims")
    op.drop_table("memory_item_tags")
    op.drop_column("memory_items", "tags_json")
