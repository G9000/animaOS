"""create user keys table

Revision ID: 20260312_0003
Revises: 20260312_0002
Create Date: 2026-03-12 00:00:01.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260312_0003"
down_revision = "20260312_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "user_keys",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kdf_salt", sa.String(length=255), nullable=False),
        sa.Column("kdf_time_cost", sa.Integer(), nullable=False),
        sa.Column("kdf_memory_cost_kib", sa.Integer(), nullable=False),
        sa.Column("kdf_parallelism", sa.Integer(), nullable=False),
        sa.Column("kdf_key_length", sa.Integer(), nullable=False),
        sa.Column("wrap_iv", sa.String(length=255), nullable=False),
        sa.Column("wrap_tag", sa.String(length=255), nullable=False),
        sa.Column("wrapped_dek", sa.String(length=1024), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_keys_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_keys")),
        sa.UniqueConstraint("user_id", name=op.f("uq_user_keys_user_id")),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("user_keys")
