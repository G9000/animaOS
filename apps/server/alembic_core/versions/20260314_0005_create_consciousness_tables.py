"""Create consciousness layer tables: self_model_blocks and emotional_signals.

Revision ID: 20260314_0005
Revises: 20260314_0004
Create Date: 2026-03-14
"""

import sqlalchemy as sa
from alembic import op

revision = "20260314_0005"
down_revision = "20260314_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "self_model_blocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_by", sa.String(32), nullable=False, server_default="system"),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "emotional_signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("emotion", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default=sa.text("0.5")),
        sa.Column("evidence_type", sa.String(24), nullable=False, server_default="linguistic"),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("trajectory", sa.String(24), nullable=False, server_default="stable"),
        sa.Column("previous_emotion", sa.String(32), nullable=True),
        sa.Column("topic", sa.String(255), nullable=False, server_default=""),
        sa.Column("acted_on", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_threads.id"], ondelete="SET NULL"),
    )


def downgrade() -> None:
    op.drop_table("emotional_signals")
    op.drop_table("self_model_blocks")
