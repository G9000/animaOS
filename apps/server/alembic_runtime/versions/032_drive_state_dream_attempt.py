"""IL7 — add last_dream_attempt_at to drive_states.

Revision ID: 032_drive_state_dream_attempt
Revises: 031_pattern_surfaced_tiebreak
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "032_drive_state_dream_attempt"
down_revision = "031_pattern_surfaced_tiebreak"
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
    if columns and "last_dream_attempt_at" not in columns:
        op.add_column(
            "drive_states",
            sa.Column("last_dream_attempt_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _drive_states_columns(bind)
    if columns and "last_dream_attempt_at" in columns:
        op.drop_column("drive_states", "last_dream_attempt_at")
