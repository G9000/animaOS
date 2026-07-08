"""Per-user re-embed completion tracking.

The global embedding_config.reembed_required flag gates a re-embed, but the
work is per-user (soul stores are per-user encrypted).  This table records
which users have finished re-embedding for the current contract cycle so the
semantic-search gate can be per-user instead of one user's completion
re-enabling everyone.

Revision ID: 026_reembed_completions
Revises: 025_consolidation_cursor
Create Date: 2026-07-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "026_reembed_completions"
down_revision = "025_consolidation_cursor"
branch_labels = None
depends_on = None

TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.TIMESTAMP(timezone=True), "sqlite"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("runtime_reembed_completions"):
        return

    op.create_table(
        "runtime_reembed_completions",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if inspector.has_table("runtime_reembed_completions"):
        op.drop_table("runtime_reembed_completions")
