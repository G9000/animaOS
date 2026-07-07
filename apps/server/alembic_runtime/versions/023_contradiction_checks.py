"""Persisted contradiction-scan verdicts.

Revision ID: 023_contradiction_checks
Revises: 022_retry_hygiene
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "023_contradiction_checks"
down_revision = "022_retry_hygiene"
branch_labels = None
depends_on = None

TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.TIMESTAMP(timezone=True), "sqlite"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("contradiction_checks"):
        return

    op.create_table(
        "contradiction_checks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("pair_hash", sa.String(length=64), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column(
            "checked_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "pair_hash", name="uq_contradiction_checks_user_pair"
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("contradiction_checks"):
        op.drop_table("contradiction_checks")
