"""add reconsolidation_drift column and reconsolidation_log table (IL6)

Revision ID: 20260719_0001
Revises: 20260718_0001
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260719_0001"
down_revision = "20260718_0001"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def upgrade() -> None:
    # Mirrors the guard in 20260718_0001_add_tendency_distillation.py: a
    # legacy or mis-stamped DB may reach this migration before memory_items
    # exists at all (session._run_alembic_upgrade's Base.metadata.create_all
    # repair step runs AFTER the migration chain and fills in any table that
    # never got created). Skipping here and letting the repair step create
    # memory_items with every current column, and _repair_legacy_memory_schema
    # backfill the column on legacy DBs stamped past this migration, is safe.
    if _has_table("memory_items"):
        op.add_column(
            "memory_items",
            sa.Column(
                "reconsolidation_drift",
                sa.Float(),
                nullable=False,
                server_default="0.0",
            ),
        )

    if _has_table("memory_items") and not _has_table("reconsolidation_log"):
        op.create_table(
            "reconsolidation_log",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("memory_item_id", sa.Integer(), nullable=False),
            sa.Column(
                "applied_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("field", sa.String(length=32), nullable=False),
            sa.Column("old_value", sa.Float(), nullable=False),
            sa.Column("new_value", sa.Float(), nullable=False),
            sa.Column("eta", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name=op.f("fk_reconsolidation_log_user_id_users"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["memory_item_id"],
                ["memory_items.id"],
                name=op.f("fk_reconsolidation_log_memory_item_id_memory_items"),
                ondelete="CASCADE",
            ),
        )
        op.create_index(
            "ix_reconsolidation_log_user_id",
            "reconsolidation_log",
            ["user_id"],
        )
        op.create_index(
            "ix_reconsolidation_log_item",
            "reconsolidation_log",
            ["memory_item_id"],
        )


def downgrade() -> None:
    if _has_table("reconsolidation_log"):
        op.drop_index("ix_reconsolidation_log_item", table_name="reconsolidation_log")
        op.drop_index("ix_reconsolidation_log_user_id", table_name="reconsolidation_log")
        op.drop_table("reconsolidation_log")
    if _has_table("memory_items"):
        op.drop_column("memory_items", "reconsolidation_drift")
