"""Persisted embedding contract (model, dim, reembed flag).

Revision ID: 024_embedding_config
Revises: 023_contradiction_checks
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "024_embedding_config"
down_revision = "023_contradiction_checks"
branch_labels = None
depends_on = None

TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.TIMESTAMP(timezone=True), "sqlite"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("embedding_config"):
        return

    op.create_table(
        "embedding_config",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("embedding_model", sa.String(length=128), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=False),
        sa.Column(
            "reembed_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("embedding_config"):
        op.drop_table("embedding_config")
