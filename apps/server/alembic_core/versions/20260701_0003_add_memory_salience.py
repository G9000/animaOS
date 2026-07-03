"""add memory salience and soft evolution fields

Revision ID: 20260701_0003
Revises: 20260630_0001
Create Date: 2026-07-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260701_0003"
down_revision = "20260630_0001"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def upgrade() -> None:
    if not _has_table("memory_items"):
        return

    with op.batch_alter_table("memory_items") as batch_op:
        batch_op.add_column(
            sa.Column(
                "memory_class",
                sa.String(length=32),
                server_default=sa.text("'casual'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "emotional_salience",
                sa.Float(),
                server_default=sa.text("0.0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "stability_class",
                sa.String(length=32),
                server_default=sa.text("'stable'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "decay_class",
                sa.String(length=32),
                server_default=sa.text("'standard'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "relationship_proximity",
                sa.Float(),
                server_default=sa.text("0.0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "evidence_strength",
                sa.Float(),
                server_default=sa.text("0.8"),
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("evolves_from_item_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("evolution_kind", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_memory_items_evolves_from_item_id_memory_items"),
            "memory_items",
            ["evolves_from_item_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index(
        "ix_memory_items_user_decay_class",
        "memory_items",
        ["user_id", "decay_class"],
    )
    op.create_index(
        "ix_memory_items_user_evolves_from",
        "memory_items",
        ["user_id", "evolves_from_item_id"],
    )


def downgrade() -> None:
    if not _has_table("memory_items"):
        return

    op.drop_index("ix_memory_items_user_evolves_from", table_name="memory_items")
    op.drop_index("ix_memory_items_user_decay_class", table_name="memory_items")

    with op.batch_alter_table("memory_items") as batch_op:
        batch_op.drop_constraint(
            op.f("fk_memory_items_evolves_from_item_id_memory_items"),
            type_="foreignkey",
        )
        batch_op.drop_column("evolution_kind")
        batch_op.drop_column("evolves_from_item_id")
        batch_op.drop_column("evidence_strength")
        batch_op.drop_column("relationship_proximity")
        batch_op.drop_column("decay_class")
        batch_op.drop_column("stability_class")
        batch_op.drop_column("emotional_salience")
        batch_op.drop_column("memory_class")
