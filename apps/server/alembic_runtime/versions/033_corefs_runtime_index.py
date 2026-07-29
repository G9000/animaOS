"""Add instance-scoped CoreFS Runtime index metadata.

Revision ID: 033_corefs_runtime_index
Revises: 032_drive_state_dream_attempt
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "033_corefs_runtime_index"
down_revision = "032_drive_state_dream_attempt"
branch_labels = None
depends_on = None


TABLES = (
    "corefs_runtime_binding",
    "corefs_index_entries",
    "corefs_index_checkpoints",
    "corefs_blind_tokens",
    "corefs_migration_journal",
    "corefs_sealed_payloads",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("corefs_runtime_binding"):
        op.create_table(
            "corefs_runtime_binding",
            sa.Column("binding_slot", sa.Integer(), nullable=False),
            sa.Column("core_id", sa.String(64), nullable=False),
            sa.Column("local_instance_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "binding_slot = 1",
                name="ck_corefs_runtime_binding_singleton",
            ),
            sa.PrimaryKeyConstraint("binding_slot"),
        )

    if not inspector.has_table("corefs_index_entries"):
        op.create_table(
            "corefs_index_entries",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("core_id", sa.String(64), nullable=False),
            sa.Column("local_instance_id", sa.String(64), nullable=False),
            sa.Column("family", sa.String(32), nullable=False),
            sa.Column("object_id_hash", sa.String(64), nullable=False),
            sa.Column("revision_hash", sa.String(64), nullable=False),
            sa.Column("catalog_generation", sa.Integer(), nullable=False),
            sa.Column("index_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("checksum", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "core_id",
                "local_instance_id",
                "family",
                "object_id_hash",
                "revision_hash",
            ),
        )
        op.create_index(
            "ix_corefs_index_entries_core_id",
            "corefs_index_entries",
            ["core_id"],
        )
        op.create_index(
            "ix_corefs_index_entries_local_instance_id",
            "corefs_index_entries",
            ["local_instance_id"],
        )

    if not inspector.has_table("corefs_index_checkpoints"):
        op.create_table(
            "corefs_index_checkpoints",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("core_id", sa.String(64), nullable=False),
            sa.Column("local_instance_id", sa.String(64), nullable=False),
            sa.Column("family", sa.String(32), nullable=False),
            sa.Column("catalog_generation", sa.Integer(), nullable=False),
            sa.Column("index_version", sa.Integer(), nullable=False),
            sa.Column("cursor_hash", sa.String(64), nullable=True),
            sa.Column("completed_count", sa.Integer(), nullable=False),
            sa.Column("total_count", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_digest", sa.String(64), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "core_id",
                "local_instance_id",
                "family",
                "catalog_generation",
                "index_version",
            ),
        )
        op.create_index(
            "ix_corefs_index_checkpoints_core_id",
            "corefs_index_checkpoints",
            ["core_id"],
        )
        op.create_index(
            "ix_corefs_index_checkpoints_local_instance_id",
            "corefs_index_checkpoints",
            ["local_instance_id"],
        )

    if not inspector.has_table("corefs_blind_tokens"):
        op.create_table(
            "corefs_blind_tokens",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("core_id", sa.String(64), nullable=False),
            sa.Column("local_instance_id", sa.String(64), nullable=False),
            sa.Column("family", sa.String(32), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=False),
            sa.Column("token", sa.LargeBinary(32), nullable=False),
            sa.Column("object_id_hash", sa.String(64), nullable=False),
            sa.Column("revision_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "core_id",
                "local_instance_id",
                "family",
                "generation",
                "token",
                "object_id_hash",
            ),
        )
        op.create_index(
            "ix_corefs_blind_tokens_core_id",
            "corefs_blind_tokens",
            ["core_id"],
        )
        op.create_index(
            "ix_corefs_blind_tokens_local_instance_id",
            "corefs_blind_tokens",
            ["local_instance_id"],
        )
        op.create_index(
            "ix_corefs_blind_tokens_token",
            "corefs_blind_tokens",
            ["token"],
        )

    if not inspector.has_table("corefs_migration_journal"):
        op.create_table(
            "corefs_migration_journal",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("core_id", sa.String(64), nullable=False),
            sa.Column("local_instance_id", sa.String(64), nullable=False),
            sa.Column("converter_id", sa.String(64), nullable=False),
            sa.Column("source_id_hash", sa.String(64), nullable=False),
            sa.Column("batch_cursor_hash", sa.String(64), nullable=True),
            sa.Column("source_checksum", sa.String(64), nullable=True),
            sa.Column("target_checksum", sa.String(64), nullable=True),
            sa.Column("migrated_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_digest", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "core_id",
                "local_instance_id",
                "converter_id",
                "source_id_hash",
            ),
        )
        op.create_index(
            "ix_corefs_migration_journal_core_id",
            "corefs_migration_journal",
            ["core_id"],
        )
        op.create_index(
            "ix_corefs_migration_journal_local_instance_id",
            "corefs_migration_journal",
            ["local_instance_id"],
        )

    if not inspector.has_table("corefs_sealed_payloads"):
        op.create_table(
            "corefs_sealed_payloads",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("core_id", sa.String(64), nullable=False),
            sa.Column("local_instance_id", sa.String(64), nullable=False),
            sa.Column("row_type", sa.String(48), nullable=False),
            sa.Column("row_id_hash", sa.String(64), nullable=False),
            sa.Column("owner_id_hash", sa.String(64), nullable=False),
            sa.Column("key_version", sa.Integer(), nullable=False),
            sa.Column("nonce", sa.LargeBinary(12), nullable=False),
            sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
            sa.Column("aad_digest", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "core_id",
                "local_instance_id",
                "row_type",
                "row_id_hash",
            ),
        )
        op.create_index(
            "ix_corefs_sealed_payloads_core_id",
            "corefs_sealed_payloads",
            ["core_id"],
        )
        op.create_index(
            "ix_corefs_sealed_payloads_local_instance_id",
            "corefs_sealed_payloads",
            ["local_instance_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table_name in reversed(TABLES):
        if inspector.has_table(table_name):
            op.drop_table(table_name)
