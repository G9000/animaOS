"""temporal knowledge graph v2

Revision ID: dbbe99c1da3a
Revises: 20260626_0002
Create Date: 2026-06-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "dbbe99c1da3a"
down_revision = "20260626_0002"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _has_foreign_key(table_name: str, constraint_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(
        foreign_key["name"] == constraint_name
        for foreign_key in inspector.get_foreign_keys(table_name)
    )


def upgrade() -> None:
    if not _has_table("kg_entities") or not _has_table("kg_relations"):
        return

    if not _has_column("kg_entities", "aliases_json"):
        with op.batch_alter_table("kg_entities") as batch_op:
            batch_op.add_column(sa.Column("aliases_json", sa.JSON(), nullable=True))

    if _has_column("kg_relations", "status"):
        return

    with op.batch_alter_table("kg_relations") as batch_op:
        batch_op.add_column(sa.Column("evidence_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column(
                "confidence",
                sa.Float(),
                nullable=False,
                server_default=sa.text("1.0"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=24),
                nullable=False,
                server_default=sa.text("'active'"),
            )
        )
        batch_op.add_column(sa.Column("supersedes_relation_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("evolves_from_relation_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_kg_relations_evidence_id_memory_item_evidence",
            "memory_item_evidence",
            ["evidence_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_kg_relations_supersedes_relation_id_kg_relations",
            "kg_relations",
            ["supersedes_relation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_kg_relations_evolves_from_relation_id_kg_relations",
            "kg_relations",
            ["evolves_from_relation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_kg_relations_evidence", ["evidence_id"])
        batch_op.create_index("ix_kg_relations_supersedes", ["supersedes_relation_id"])
        batch_op.create_index("ix_kg_relations_evolves_from", ["evolves_from_relation_id"])
        batch_op.create_index("ix_kg_relations_user_status", ["user_id", "status"])
        batch_op.create_index(
            "ix_kg_relations_user_source_type",
            ["user_id", "source_id", "relation_type"],
        )
        batch_op.create_index("ix_kg_relations_user_observed", ["user_id", "observed_at"])

    op.execute(
        """
        UPDATE kg_relations
        SET
            observed_at = COALESCE(observed_at, created_at),
            valid_from = COALESCE(valid_from, created_at),
            confidence = COALESCE(confidence, 1.0),
            status = COALESCE(status, 'active')
        """
    )


def downgrade() -> None:
    if _has_table("kg_relations") and _has_column("kg_relations", "status"):
        with op.batch_alter_table("kg_relations") as batch_op:
            for index_name in (
                "ix_kg_relations_user_observed",
                "ix_kg_relations_user_source_type",
                "ix_kg_relations_user_status",
                "ix_kg_relations_evolves_from",
                "ix_kg_relations_supersedes",
                "ix_kg_relations_evidence",
            ):
                if _has_index("kg_relations", index_name):
                    batch_op.drop_index(index_name)
            for constraint_name in (
                "fk_kg_relations_evolves_from_relation_id_kg_relations",
                "fk_kg_relations_supersedes_relation_id_kg_relations",
                "fk_kg_relations_evidence_id_memory_item_evidence",
            ):
                if _has_foreign_key("kg_relations", constraint_name):
                    batch_op.drop_constraint(constraint_name, type_="foreignkey")
            batch_op.drop_column("evolves_from_relation_id")
            batch_op.drop_column("supersedes_relation_id")
            batch_op.drop_column("status")
            batch_op.drop_column("confidence")
            batch_op.drop_column("valid_to")
            batch_op.drop_column("valid_from")
            batch_op.drop_column("observed_at")
            batch_op.drop_column("evidence_id")

    if _has_table("kg_entities") and _has_column("kg_entities", "aliases_json"):
        with op.batch_alter_table("kg_entities") as batch_op:
            batch_op.drop_column("aliases_json")
