"""IL7 — add dream_sharing to presence_configs + dream_journal table

Revision ID: 20260721_0001
Revises: 20260720_0001
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260721_0001"
down_revision = "20260720_0001"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _presence_config_columns() -> set[str]:
    if not _has_table("presence_configs"):
        return set()
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns("presence_configs")}


def upgrade() -> None:
    # Same guard style as 20260720_0001: a legacy/mis-stamped DB may reach this
    # migration before presence_configs exists; create_all (run after the chain
    # in db/session.py._run_alembic_upgrade) fills in any missing table, and the
    # legacy column-repair guard backfills the column on DBs stamped past here.
    existing_columns = _presence_config_columns()
    if existing_columns and "dream_sharing" not in existing_columns:
        op.add_column(
            "presence_configs",
            sa.Column(
                "dream_sharing",
                sa.String(length=16),
                nullable=False,
                server_default="on_ask",
            ),
        )

    if not _has_table("dream_journal"):
        op.create_table(
            "dream_journal",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column(
                "dreamt_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("narrative", sa.Text(), nullable=False),
            sa.Column("source_refs", sa.JSON(), nullable=False),
            sa.Column("affect_delta", sa.JSON(), nullable=False),
            sa.Column("share_worthy", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("surfaced", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name=op.f("fk_dream_journal_user_id_users"),
                ondelete="CASCADE",
            ),
        )
        op.create_index(
            "ix_dream_journal_user_dreamt",
            "dream_journal",
            ["user_id", "dreamt_at"],
        )


def downgrade() -> None:
    if _has_table("dream_journal"):
        op.drop_index("ix_dream_journal_user_dreamt", table_name="dream_journal")
        op.drop_table("dream_journal")

    existing_columns = _presence_config_columns()
    if "dream_sharing" in existing_columns:
        op.drop_column("presence_configs", "dream_sharing")
