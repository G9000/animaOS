"""P3: Add consciousness tables to runtime."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "working_context",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("section", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_by", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "section", name="uq_working_context_user_section"),
    )

    op.create_table(
        "active_intentions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_by", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "current_emotions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "thread_id",
            sa.BigInteger(),
            sa.ForeignKey("runtime_threads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("emotion", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "evidence_type",
            sa.String(length=24),
            nullable=False,
            server_default="linguistic",
        ),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "trajectory",
            sa.String(length=24),
            nullable=False,
            server_default="stable",
        ),
        sa.Column("previous_emotion", sa.String(length=32), nullable=True),
        sa.Column("topic", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("acted_on", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_current_emotions_user_created",
        "current_emotions",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_current_emotions_user_created", table_name="current_emotions")
    op.drop_table("current_emotions")
    op.drop_table("active_intentions")
    op.drop_table("working_context")
