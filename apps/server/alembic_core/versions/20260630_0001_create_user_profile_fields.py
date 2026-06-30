"""create structured user profile fields

Revision ID: 20260630_0001
Revises: dbbe99c1da3a
Create Date: 2026-06-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260630_0001"
down_revision = "dbbe99c1da3a"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return False
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("user_profile_fields"):
        op.create_table(
            "user_profile_fields",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("category", sa.String(length=32), nullable=False),
            sa.Column("key", sa.String(length=128), nullable=False),
            sa.Column("value_text", sa.Text(), nullable=False),
            sa.Column(
                "confidence",
                sa.Float(),
                server_default=sa.text("0.8"),
                nullable=False,
            ),
            sa.Column(
                "status",
                sa.String(length=16),
                server_default=sa.text("'active'"),
                nullable=False,
            ),
            sa.Column(
                "source_kind",
                sa.String(length=32),
                server_default=sa.text("'extraction'"),
                nullable=False,
            ),
            sa.Column("source_memory_id", sa.Integer(), nullable=True),
            sa.Column("source_evidence_id", sa.Integer(), nullable=True),
            sa.Column("source_claim_evidence_id", sa.Integer(), nullable=True),
            sa.Column("superseded_by_id", sa.Integer(), nullable=True),
            sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.ForeignKeyConstraint(
                ["source_evidence_id"],
                ["memory_item_evidence.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["source_claim_evidence_id"],
                ["memory_claim_evidence.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["source_memory_id"],
                ["memory_items.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["superseded_by_id"],
                ["user_profile_fields.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_index("user_profile_fields", "ix_user_profile_fields_user_status"):
        op.create_index(
            "ix_user_profile_fields_user_status",
            "user_profile_fields",
            ["user_id", "status"],
        )
    if not _has_index("user_profile_fields", "ix_user_profile_fields_user_category_key"):
        op.create_index(
            "ix_user_profile_fields_user_category_key",
            "user_profile_fields",
            ["user_id", "category", "key"],
        )
    if not _has_index("user_profile_fields", "ix_user_profile_fields_superseded_by"):
        op.create_index(
            "ix_user_profile_fields_superseded_by",
            "user_profile_fields",
            ["superseded_by_id"],
        )

    if not _has_table("user_profile_field_evidence"):
        op.create_table(
            "user_profile_field_evidence",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("profile_field_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column(
                "source_kind",
                sa.String(length=32),
                server_default=sa.text("'extraction'"),
                nullable=False,
            ),
            sa.Column("source_memory_id", sa.Integer(), nullable=True),
            sa.Column("source_evidence_id", sa.Integer(), nullable=True),
            sa.Column("source_claim_evidence_id", sa.Integer(), nullable=True),
            sa.Column("runtime_thread_id", sa.Integer(), nullable=True),
            sa.Column("runtime_message_id", sa.Integer(), nullable=True),
            sa.Column("evidence_text", sa.Text(), nullable=False),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["profile_field_id"],
                ["user_profile_fields.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["source_evidence_id"],
                ["memory_item_evidence.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["source_claim_evidence_id"],
                ["memory_claim_evidence.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["source_memory_id"],
                ["memory_items.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_index(
        "user_profile_field_evidence",
        "ix_user_profile_field_evidence_user_field",
    ):
        op.create_index(
            "ix_user_profile_field_evidence_user_field",
            "user_profile_field_evidence",
            ["user_id", "profile_field_id"],
        )
    if not _has_index(
        "user_profile_field_evidence",
        "ix_user_profile_field_evidence_user_observed",
    ):
        op.create_index(
            "ix_user_profile_field_evidence_user_observed",
            "user_profile_field_evidence",
            ["user_id", "observed_at"],
        )
    if not _has_index(
        "user_profile_field_evidence",
        "ix_user_profile_field_evidence_memory",
    ):
        op.create_index(
            "ix_user_profile_field_evidence_memory",
            "user_profile_field_evidence",
            ["source_memory_id"],
        )
    if not _has_index(
        "user_profile_field_evidence",
        "ix_user_profile_field_evidence_source_evidence",
    ):
        op.create_index(
            "ix_user_profile_field_evidence_source_evidence",
            "user_profile_field_evidence",
            ["source_evidence_id"],
        )
    if not _has_index(
        "user_profile_field_evidence",
        "ix_user_profile_field_evidence_source_claim_evidence",
    ):
        op.create_index(
            "ix_user_profile_field_evidence_source_claim_evidence",
            "user_profile_field_evidence",
            ["source_claim_evidence_id"],
        )


def downgrade() -> None:
    if _has_table("user_profile_field_evidence"):
        for index_name in (
            "ix_user_profile_field_evidence_source_claim_evidence",
            "ix_user_profile_field_evidence_source_evidence",
            "ix_user_profile_field_evidence_memory",
            "ix_user_profile_field_evidence_user_observed",
            "ix_user_profile_field_evidence_user_field",
        ):
            if _has_index("user_profile_field_evidence", index_name):
                op.drop_index(index_name, table_name="user_profile_field_evidence")
        op.drop_table("user_profile_field_evidence")

    if _has_table("user_profile_fields"):
        for index_name in (
            "ix_user_profile_fields_superseded_by",
            "ix_user_profile_fields_user_category_key",
            "ix_user_profile_fields_user_status",
        ):
            if _has_index("user_profile_fields", index_name):
                op.drop_index(index_name, table_name="user_profile_fields")
        op.drop_table("user_profile_fields")
