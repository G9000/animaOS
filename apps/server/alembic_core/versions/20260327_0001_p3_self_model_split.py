"""P3: Self-model split - create identity, growth, and emotional pattern tables.

Migrates identity rows out of ``self_model_blocks`` and removes the sections
that move to runtime storage.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260327_0001"
down_revision = "20260324_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_blocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_by", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_identity_blocks_user_id"),
    )

    op.create_table(
        "growth_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="sleep_time"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "core_emotional_patterns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("dominant_emotion", sa.String(length=32), nullable=False),
        sa.Column("trigger_context", sa.Text(), nullable=False, server_default=""),
        sa.Column("frequency", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column(
            "first_observed",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_observed",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.execute(
        """
        INSERT INTO identity_blocks (user_id, content, version, updated_by, created_at, updated_at)
        SELECT user_id, content, version, updated_by, created_at, updated_at
        FROM self_model_blocks
        WHERE section = 'identity'
        """
    )

    op.execute(
        """
        DELETE FROM self_model_blocks
        WHERE section IN ('identity', 'growth_log', 'inner_state', 'working_memory', 'intentions')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        INSERT INTO self_model_blocks (user_id, section, content, version, updated_by, created_at, updated_at)
        SELECT user_id, 'identity', content, version, updated_by, created_at, updated_at
        FROM identity_blocks
        """
    )
    op.drop_table("core_emotional_patterns")
    op.drop_table("growth_log")
    op.drop_table("identity_blocks")
