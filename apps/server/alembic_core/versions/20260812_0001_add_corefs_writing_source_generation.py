"""Add a transactional source generation for legacy diary writing rows.

Revision ID: 20260812_0001
Revises: 20260802_0001
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260812_0001"
down_revision = "20260802_0001"
branch_labels = None
depends_on = None

_WRITING_TABLES = ("diary_folders", "diary_entries", "diary_attachments")


def _bump_statement(user_expression: str) -> str:
    return f"""
        INSERT INTO corefs_writing_source_state (user_id, generation)
        VALUES ({user_expression}, 1)
        ON CONFLICT(user_id) DO UPDATE
        SET generation = corefs_writing_source_state.generation + 1;
    """


def _create_triggers(table_name: str) -> None:
    for operation, row in (("insert", "NEW"), ("update", "NEW"), ("delete", "OLD")):
        op.execute(
            sa.text(
                f"""
                CREATE TRIGGER trg_corefs_writing_source_{table_name}_{operation}
                AFTER {operation.upper()} ON {table_name}
                BEGIN
                    {_bump_statement(f"{row}.user_id")}
                END
                """
            )
        )

    # A reassignment changes both owners' source inventories. Normal UPDATEs
    # increment only NEW.user_id through the trigger above; this extra trigger
    # accounts for the OLD.user_id only when ownership actually changes.
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER trg_corefs_writing_source_{table_name}_update_old_user
            AFTER UPDATE OF user_id ON {table_name}
            WHEN OLD.user_id <> NEW.user_id
            BEGIN
                {_bump_statement("OLD.user_id")}
            END
            """
        )
    )


def upgrade() -> None:
    op.create_table(
        "corefs_writing_source_state",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "generation >= 1",
            name=op.f("ck_corefs_writing_source_state_generation_positive"),
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_corefs_writing_source_state")),
    )
    for table_name in _WRITING_TABLES:
        _create_triggers(table_name)


def downgrade() -> None:
    for table_name in reversed(_WRITING_TABLES):
        for suffix in ("update_old_user", "delete", "update", "insert"):
            op.execute(
                sa.text(f"DROP TRIGGER IF EXISTS trg_corefs_writing_source_{table_name}_{suffix}")
            )
    op.drop_table("corefs_writing_source_state")
