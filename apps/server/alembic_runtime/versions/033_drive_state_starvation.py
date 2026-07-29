"""IL-013 — add starvation_losses to drive_states.

Revision ID: 033_drive_state_starvation
Revises: 032_drive_state_dream_attempt
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "033_drive_state_starvation"
down_revision = "032_drive_state_dream_attempt"
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
    if columns and "starvation_losses" not in columns:
        op.add_column(
            "drive_states",
            sa.Column("starvation_losses", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _drive_states_columns(bind)
    if columns and "starvation_losses" in columns:
        op.drop_column("drive_states", "starvation_losses")
