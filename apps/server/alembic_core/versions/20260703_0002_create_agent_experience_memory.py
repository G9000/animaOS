"""create agent experience memory

Revision ID: 20260703_0002
Revises: 20260703_0001
Create Date: 2026-07-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260703_0002"
down_revision = "20260703_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_experiences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_intent", sa.Text(), nullable=False),
        sa.Column("approach", sa.Text(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("source_thread_id", sa.Integer(), nullable=True),
        sa.Column("source_run_id", sa.Integer(), nullable=True),
        sa.Column("tool_names_json", sa.JSON(), nullable=True),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("embedding_json", sa.JSON(), nullable=True),
        sa.Column("cluster_id", sa.String(length=64), nullable=True),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["agent_experiences.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_agent_experiences_user", "agent_experiences", ["user_id"])
    op.create_index(
        "ix_agent_experiences_user_cluster",
        "agent_experiences",
        ["user_id", "cluster_id"],
    )
    op.create_index(
        "ix_agent_experiences_user_active",
        "agent_experiences",
        ["user_id", "superseded_by"],
    )

    op.create_table(
        "experience_cluster_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_experience_cluster_state_user"),
    )

    op.create_table(
        "agent_skills",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("cluster_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("experience_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_refined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding_json", sa.JSON(), nullable=True),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["superseded_by"], ["agent_skills.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_agent_skills_user_cluster",
        "agent_skills",
        ["user_id", "cluster_id"],
    )
    op.create_index(
        "ix_agent_skills_user_active",
        "agent_skills",
        ["user_id", "superseded_by"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_skills_user_active", table_name="agent_skills")
    op.drop_index("ix_agent_skills_user_cluster", table_name="agent_skills")
    op.drop_table("agent_skills")
    op.drop_table("experience_cluster_state")
    op.drop_index("ix_agent_experiences_user_active", table_name="agent_experiences")
    op.drop_index("ix_agent_experiences_user_cluster", table_name="agent_experiences")
    op.drop_index("ix_agent_experiences_user", table_name="agent_experiences")
    op.drop_table("agent_experiences")
