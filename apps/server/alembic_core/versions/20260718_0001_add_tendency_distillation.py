"""add tendency_contributions ledger and memory_items.distilled_at (IL5)

Revision ID: 20260718_0001
Revises: 20260717_0001
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260718_0001"
down_revision = "20260717_0001"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def upgrade() -> None:
    # Mirrors the guard in 20260701_0003_add_memory_salience.py: a legacy or
    # mis-stamped DB may reach this migration before memory_items/
    # memory_claims exist at all (e.g. session._run_alembic_upgrade's
    # Base.metadata.create_all repair step, which runs AFTER the migration
    # chain and fills in any table that never got created). Altering/
    # FK-referencing a table that doesn't exist yet would crash the whole
    # upgrade before that repair step ever runs; skipping here and letting
    # the repair step create both tables (already carrying every current
    # column, since it builds from the live ORM models) is safe and additive.
    if _has_table("memory_items"):
        op.add_column(
            "memory_items",
            sa.Column("distilled_at", sa.DateTime(timezone=True), nullable=True),
        )

    if _has_table("memory_items") and _has_table("memory_claims") and not _has_table(
        "tendency_contributions"
    ):
        op.create_table(
            "tendency_contributions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("tombstone_item_id", sa.Integer(), nullable=False),
            sa.Column("tendency_claim_id", sa.Integer(), nullable=False),
            sa.Column("contribution_vector", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            # One tombstone distills into exactly one tendency: a unique
            # tombstone_item_id makes concurrent sleep pipelines that both
            # select the same not-yet-distilled item safe — the loser's
            # ledger insert fails and its per-item transaction rolls back,
            # instead of double-counting the contribution and inflating the
            # tendency's strength (which would break exact right-to-forget).
            sa.UniqueConstraint(
                "tombstone_item_id",
                name=op.f("uq_tendency_contributions_tombstone_item_id"),
            ),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name=op.f("fk_tendency_contributions_user_id_users"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["tombstone_item_id"],
                ["memory_items.id"],
                name=op.f("fk_tendency_contributions_tombstone_item_id_memory_items"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["tendency_claim_id"],
                ["memory_claims.id"],
                name=op.f("fk_tendency_contributions_tendency_claim_id_memory_claims"),
                ondelete="CASCADE",
            ),
        )
        op.create_index(
            "ix_tendency_contributions_user_id",
            "tendency_contributions",
            ["user_id"],
        )
        op.create_index(
            "ix_tendency_contributions_claim",
            "tendency_contributions",
            ["tendency_claim_id"],
        )


def downgrade() -> None:
    if _has_table("tendency_contributions"):
        op.drop_index("ix_tendency_contributions_claim", table_name="tendency_contributions")
        op.drop_index("ix_tendency_contributions_user_id", table_name="tendency_contributions")
        op.drop_table("tendency_contributions")
    if _has_table("memory_items"):
        op.drop_column("memory_items", "distilled_at")
