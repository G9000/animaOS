"""Offline catch-up audit rows (IL2: presence tick and offline catch-up).

Revision ID: 028_presence_catchup
Revises: 027_affect_state
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "028_presence_catchup"
down_revision = "027_affect_state"
branch_labels = None
depends_on = None

TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.TIMESTAMP(timezone=True), "sqlite"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("presence_catchup"):
        return

    op.create_table(
        "presence_catchup",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("gap_seconds", sa.Float(), nullable=False),
        sa.Column(
            "components",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "dream_deferred",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_presence_catchup_user_id",
        "presence_catchup",
        ["user_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("presence_catchup"):
        op.drop_index("ix_presence_catchup_user_id", table_name="presence_catchup")
        op.drop_table("presence_catchup")
