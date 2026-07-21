"""IL3 — drive_states and pending_initiatives tables.

Revision ID: 030_drive_state_pending_initiative
Revises: 029_folded_candidate_hash
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "030_drive_state_pending_initiative"
down_revision = "029_folded_candidate_hash"
branch_labels = None
depends_on = None

TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.TIMESTAMP(timezone=True), "sqlite"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("drive_states"):
        op.create_table(
            "drive_states",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column(
                "unresolved_thread", sa.Float(), nullable=False, server_default="0.0"
            ),
            sa.Column(
                "pattern_insight", sa.Float(), nullable=False, server_default="0.0"
            ),
            sa.Column("relational", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("novelty", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column(
                "dream_residue", sa.Float(), nullable=False, server_default="0.0"
            ),
            sa.Column("last_fired_at", TIMESTAMPTZ, nullable=True),
            sa.Column("last_user_turn_at", TIMESTAMPTZ, nullable=True),
            sa.Column("pattern_insight_surfaced_at", TIMESTAMPTZ, nullable=True),
            sa.Column(
                "unanswered_initiatives",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "updated_at",
                TIMESTAMPTZ,
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", name="uq_drive_states_user_id"),
        )

    if not inspector.has_table("pending_initiatives"):
        op.create_table(
            "pending_initiatives",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("initiative_log_id", sa.BigInteger(), nullable=False),
            sa.Column("drive", sa.String(length=32), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column(
                "delivered", sa.Boolean(), nullable=False, server_default=sa.text("false")
            ),
            sa.Column(
                "acknowledged",
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
            sa.Column("acknowledged_at", TIMESTAMPTZ, nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_pending_initiatives_user_id",
            "pending_initiatives",
            ["user_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("pending_initiatives"):
        op.drop_index("ix_pending_initiatives_user_id", table_name="pending_initiatives")
        op.drop_table("pending_initiatives")

    if inspector.has_table("drive_states"):
        op.drop_table("drive_states")
