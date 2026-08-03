"""IL-015 — add dream_journal.claimed_at for the claim/acknowledge protocol.

Revision ID: 20260802_0001
Revises: 20260721_0001
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260802_0001"
down_revision = "20260721_0001"
branch_labels = None
depends_on = None


def _dream_journal_columns(bind) -> set[str]:
    inspector = inspect(bind)
    if not inspector.has_table("dream_journal"):
        return set()
    return {col["name"] for col in inspector.get_columns("dream_journal")}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _dream_journal_columns(bind)
    if columns and "claimed_at" not in columns:
        op.add_column(
            "dream_journal",
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _dream_journal_columns(bind)
    if columns and "claimed_at" in columns:
        op.drop_column("dream_journal", "claimed_at")
