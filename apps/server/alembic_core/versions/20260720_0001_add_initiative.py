"""IL3 — add initiative opt-in fields to presence_configs + initiative_log table

Revision ID: 20260720_0001
Revises: 20260719_0001
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260720_0001"
down_revision = "20260719_0001"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def _presence_config_columns() -> set[str]:
    if not _has_table("presence_configs"):
        return set()
    return {col["name"] for col in sa.inspect(op.get_bind()).get_columns("presence_configs")}


def upgrade() -> None:
    # Mirrors the guard style in 20260719_0001_add_reconsolidation.py: a
    # legacy or mis-stamped DB may reach this migration before
    # presence_configs exists at all — the repair step in
    # db/session.py._run_alembic_upgrade (Base.metadata.create_all, which
    # runs AFTER the migration chain) fills in any table that never got
    # created, and the legacy column-repair guard below backfills columns
    # on legacy DBs stamped past this migration.
    existing_columns = _presence_config_columns()
    if existing_columns:
        if "initiative_enabled" not in existing_columns:
            op.add_column(
                "presence_configs",
                sa.Column(
                    "initiative_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default="0",
                ),
            )
        if "quiet_hours_start" not in existing_columns:
            op.add_column(
                "presence_configs", sa.Column("quiet_hours_start", sa.Integer(), nullable=True)
            )
        if "quiet_hours_end" not in existing_columns:
            op.add_column(
                "presence_configs", sa.Column("quiet_hours_end", sa.Integer(), nullable=True)
            )

    if not _has_table("initiative_log"):
        op.create_table(
            "initiative_log",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column(
                "fired_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("drive", sa.String(length=32), nullable=False),
            sa.Column("pressure_snapshot", sa.JSON(), nullable=False),
            sa.Column("gate_states", sa.JSON(), nullable=False),
            sa.Column("generated_text", sa.Text(), nullable=True),
            sa.Column("delivered", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("answered", sa.Boolean(), nullable=False, server_default="0"),
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
                name=op.f("fk_initiative_log_user_id_users"),
                ondelete="CASCADE",
            ),
        )
        op.create_index(
            "ix_initiative_log_user_fired",
            "initiative_log",
            ["user_id", "fired_at"],
        )


def downgrade() -> None:
    if _has_table("initiative_log"):
        op.drop_index("ix_initiative_log_user_fired", table_name="initiative_log")
        op.drop_table("initiative_log")

    existing_columns = _presence_config_columns()
    if existing_columns:
        for column_name in ("quiet_hours_end", "quiet_hours_start", "initiative_enabled"):
            if column_name in existing_columns:
                op.drop_column("presence_configs", column_name)
