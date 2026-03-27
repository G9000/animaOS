"""create agent_profile table

Revision ID: 20260316_0001
Revises: 20260314_0006
Create Date: 2026-03-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260316_0001"
down_revision = "20260314_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_profile",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(length=50), nullable=False, server_default="Anima"),
        sa.Column("creator_name", sa.String(length=100), nullable=False, server_default=""),
        sa.Column(
            "relationship", sa.String(length=100), nullable=False, server_default="companion"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_agent_profile_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_profile")),
        sa.UniqueConstraint("user_id", name=op.f("uq_agent_profile_user_id")),
    )


def downgrade() -> None:
    op.drop_table("agent_profile")
