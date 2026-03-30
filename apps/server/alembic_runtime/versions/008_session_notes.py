"""Create runtime_session_notes table.

Revision ID: 008_session_notes
Revises: 007_pending_ops_hash
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "008_session_notes"
down_revision = "007_pending_ops_hash"
branch_labels = None
depends_on = None

TIMESTAMPTZ = TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "runtime_session_notes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "thread_id",
            sa.BigInteger,
            sa.ForeignKey("runtime_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("note_type", sa.String(24), nullable=False, server_default="observation"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("promoted_to_item_id", sa.Integer, nullable=True),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_runtime_session_notes_thread_active",
        "runtime_session_notes",
        ["thread_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_session_notes_thread_active", table_name="runtime_session_notes")
    op.drop_table("runtime_session_notes")
