"""create latent traces table (IL4)

Revision ID: 20260717_0001
Revises: 20260712_0001
Create Date: 2026-07-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260717_0001"
down_revision = "20260712_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "latent_traces",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("topic_key", sa.String(length=255), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=32),
            server_default=sa.text("'observation'"),
            nullable=False,
        ),
        sa.Column(
            "weight",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
        sa.Column("evidence_refs", sa.JSON(), nullable=True),
        sa.Column(
            "first_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_latent_traces_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "topic_key", name="uq_latent_traces_user_topic"),
    )
    op.create_index(
        "ix_latent_traces_user_weight",
        "latent_traces",
        ["user_id", "weight"],
    )


def downgrade() -> None:
    op.drop_index("ix_latent_traces_user_weight", table_name="latent_traces")
    op.drop_table("latent_traces")
