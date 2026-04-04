"""Drop memory_daily_logs table — redundant with RuntimeMessage.

Revision ID: 20260330_drop_daily_logs
Revises: 20260328_0002
Create Date: 2026-03-30
"""
import sqlalchemy as sa
from alembic import op

revision = "20260330_drop_daily_logs"
down_revision = "20260328_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The index ix_memory_daily_logs_user_date may or may not exist
    # (it was defined in the ORM model but not in the migration that
    # created the table).  DROP TABLE removes any associated indexes
    # automatically, so we just drop the table directly.
    op.drop_table("memory_daily_logs")


def downgrade() -> None:
    op.create_table(
        "memory_daily_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.String(10), nullable=False),
        sa.Column("user_message", sa.Text, nullable=False),
        sa.Column("assistant_response", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_memory_daily_logs_user_date",
        "memory_daily_logs",
        ["user_id", "date"],
    )
