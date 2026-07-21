"""IL3 — add pattern_insight_surfaced_id tie-breaker to drive_states.

Revision ID: 031_pattern_surfaced_tiebreak
Revises: 030_drive_state_pending_initiative
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "031_pattern_surfaced_tiebreak"
down_revision = "030_drive_state_pending_initiative"
branch_labels = None
depends_on = None


def _drive_states_columns(bind) -> set[str]:
    inspector = inspect(bind)
    if not inspector.has_table("drive_states"):
        return set()
    return {col["name"] for col in inspector.get_columns("drive_states")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _drive_states_columns(bind)
    if columns and "pattern_insight_surfaced_id" not in columns:
        op.add_column(
            "drive_states",
            sa.Column("pattern_insight_surfaced_id", sa.BigInteger(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _drive_states_columns(bind)
    if columns and "pattern_insight_surfaced_id" in columns:
        op.drop_column("drive_states", "pattern_insight_surfaced_id")
