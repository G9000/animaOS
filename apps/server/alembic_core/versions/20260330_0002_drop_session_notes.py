"""Drop session_notes table — moved to PG as runtime_session_notes.

Revision ID: 20260330_0002
Revises: 20260330_drop_daily_logs
Create Date: 2026-03-30
"""
from alembic import op
import sqlalchemy as sa

revision = "20260330_0002"
down_revision = "20260330_drop_daily_logs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("session_notes")


def downgrade() -> None:
    op.create_table(
        "session_notes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "thread_id",
            sa.Integer,
            sa.ForeignKey("agent_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("note_type", sa.String(24), nullable=False, server_default="observation"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "promoted_to_item_id",
            sa.Integer,
            sa.ForeignKey("memory_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
    )
