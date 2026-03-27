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
        sa.Column("metadata_json", sa.JSON(), nullable=True),
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

    # --- Migrate identity rows ---
    op.execute(
        """
        INSERT INTO identity_blocks (user_id, content, version, updated_by, created_at, updated_at)
        SELECT user_id, content, version, updated_by, created_at, updated_at
        FROM self_model_blocks
        WHERE section = 'identity'
        """
    )

    # --- Migrate growth_log blobs into individual rows ---
    # Growth log content uses "### YYYY-MM-DD" separators.  We cannot
    # reliably split markdown in pure SQL, so we copy the entire blob as
    # a single entry.  The application-level _replace_growth_log_entries()
    # will re-split it on the next write.  This preserves all content.
    op.execute(
        """
        INSERT INTO growth_log (user_id, entry, source, created_at)
        SELECT user_id, content, 'migration', created_at
        FROM self_model_blocks
        WHERE section = 'growth_log' AND content != ''
        """
    )

    # --- Delete migrated/moved sections ---
    op.execute(
        """
        DELETE FROM self_model_blocks
        WHERE section IN ('identity', 'growth_log', 'inner_state', 'working_memory', 'intentions')
        """
    )


def downgrade() -> None:
    # Restore identity rows to self_model_blocks
    op.execute(
        """
        INSERT INTO self_model_blocks (user_id, section, content, version, updated_by, created_at, updated_at)
        SELECT user_id, 'identity', content, version, updated_by, created_at, updated_at
        FROM identity_blocks
        """
    )

    # Restore growth_log: concatenate individual entries back into a single
    # markdown blob per user.  SQLite's group_concat preserves content; the
    # "### " prefix re-creates the original separator format.
    op.execute(
        """
        INSERT INTO self_model_blocks (user_id, section, content, version, updated_by, created_at, updated_at)
        SELECT
            user_id,
            'growth_log',
            group_concat('### ' || entry, char(10) || char(10)),
            1,
            'migration',
            MIN(created_at),
            MAX(created_at)
        FROM growth_log
        GROUP BY user_id
        """
    )

    # Re-seed empty runtime sections so the app doesn't break.
    # The actual data lives in PG runtime tables (separate migration)
    # and will be re-seeded by ensure_self_model_exists() on next startup.
    op.execute(
        """
        INSERT INTO self_model_blocks (user_id, section, content, version, updated_by)
        SELECT DISTINCT user_id, 'inner_state', '', 1, 'system'
        FROM identity_blocks
        WHERE user_id NOT IN (
            SELECT user_id FROM self_model_blocks WHERE section = 'inner_state'
        )
        """
    )
    op.execute(
        """
        INSERT INTO self_model_blocks (user_id, section, content, version, updated_by)
        SELECT DISTINCT user_id, 'working_memory', '', 1, 'system'
        FROM identity_blocks
        WHERE user_id NOT IN (
            SELECT user_id FROM self_model_blocks WHERE section = 'working_memory'
        )
        """
    )
    op.execute(
        """
        INSERT INTO self_model_blocks (user_id, section, content, version, updated_by)
        SELECT DISTINCT user_id, 'intentions', '', 1, 'system'
        FROM identity_blocks
        WHERE user_id NOT IN (
            SELECT user_id FROM self_model_blocks WHERE section = 'intentions'
        )
        """
    )

    op.drop_table("core_emotional_patterns")
    op.drop_table("growth_log")
    op.drop_table("identity_blocks")
