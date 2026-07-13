"""Create owner-scoped Soul keyslots for credential generations.

Revision ID: 20260712_0001
Revises: 20260704_0001
Create Date: 2026-07-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260712_0001"
down_revision = "20260704_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "soul_keyslots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("domain", sa.String(64), nullable=False),
        sa.Column("wrapping_path", sa.String(16), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("credential_generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("kdf_algorithm", sa.String(32), nullable=False),
        sa.Column("wrap_algorithm", sa.String(32), nullable=False),
        sa.Column("envelope_version", sa.Integer(), nullable=False),
        sa.Column("kdf_salt", sa.String(255), nullable=False),
        sa.Column("kdf_time_cost", sa.Integer(), nullable=False),
        sa.Column("kdf_memory_cost_kib", sa.Integer(), nullable=False),
        sa.Column("kdf_parallelism", sa.Integer(), nullable=False),
        sa.Column("kdf_key_length", sa.Integer(), nullable=False),
        sa.Column("wrap_iv", sa.String(255), nullable=False),
        sa.Column("wrap_tag", sa.String(255), nullable=False),
        sa.Column("wrapped_dek", sa.String(1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "wrapping_path IN ('password', 'recovery')",
            name="ck_soul_keyslots_wrapping_path",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'decrypt-only')",
            name="ck_soul_keyslots_status",
        ),
        sa.CheckConstraint("key_version > 0", name="ck_soul_keyslots_key_version"),
        sa.CheckConstraint(
            "credential_generation > 0",
            name="ck_soul_keyslots_credential_generation",
        ),
        sa.UniqueConstraint(
            "owner_id",
            "domain",
            "wrapping_path",
            "key_version",
            "credential_generation",
            "status",
            name="uq_soul_keyslots_identity_status",
        ),
    )
    op.create_index("ix_soul_keyslots_owner_id", "soul_keyslots", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_soul_keyslots_owner_id", table_name="soul_keyslots")
    op.drop_table("soul_keyslots")
