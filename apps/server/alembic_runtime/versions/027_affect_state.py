"""Persisted affect state vector (IL1: valence/arousal/energy dynamics).

Revision ID: 027_affect_state
Revises: 026_reembed_completions
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "027_affect_state"
down_revision = "026_reembed_completions"
branch_labels = None
depends_on = None

TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.TIMESTAMP(timezone=True), "sqlite"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("affect_state"):
        return

    op.create_table(
        "affect_state",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("valence", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("arousal", sa.Float(), nullable=False, server_default=sa.text("0.35")),
        sa.Column("energy", sa.Float(), nullable=False, server_default=sa.text("0.6")),
        sa.Column(
            "arousal_baseline_shift",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column(
            "high_arousal_hours",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
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
    if inspector.has_table("affect_state"):
        op.drop_table("affect_state")
