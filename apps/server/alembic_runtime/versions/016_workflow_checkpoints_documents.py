"""Add workflow checkpoint runtime tables.

Revision ID: 016_workflow_checkpoints_documents
Revises: 015_memory_extraction_failures
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "016_workflow_checkpoints_documents"
down_revision = "015_memory_extraction_failures"
branch_labels = None
depends_on = None

JSON = postgresql.JSON(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")
TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.DateTime(timezone=True),
    "sqlite",
)


def upgrade() -> None:
    op.create_table(
        "runtime_workflow_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("workflow_type", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'created'"),
            nullable=False,
        ),
        sa.Column(
            "current_state",
            sa.String(length=64),
            server_default=sa.text("'created'"),
            nullable=False,
        ),
        sa.Column("input_json", JSON, nullable=True),
        sa.Column("result_json", JSON, nullable=True),
        sa.Column("error_json", JSON, nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column(
            "created_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", TIMESTAMPTZ, nullable=True),
        sa.Column("completed_at", TIMESTAMPTZ, nullable=True),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["runtime_threads.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runtime_workflow_runs_user_id",
        "runtime_workflow_runs",
        ["user_id"],
    )
    op.create_index(
        "ix_runtime_workflow_runs_thread_id",
        "runtime_workflow_runs",
        ["thread_id"],
    )
    op.create_index(
        "ix_runtime_workflow_runs_user_status",
        "runtime_workflow_runs",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_runtime_workflow_runs_user_type",
        "runtime_workflow_runs",
        ["user_id", "workflow_type"],
    )

    op.create_table(
        "runtime_workflow_checkpoints",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=False),
        sa.Column("checkpoint_index", sa.Integer(), nullable=False),
        sa.Column("state_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input_json", JSON, nullable=True),
        sa.Column("output_json", JSON, nullable=True),
        sa.Column("artifact_refs_json", JSON, nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("error_json", JSON, nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMPTZ,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["runtime_workflow_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_run_id",
            "checkpoint_index",
            name="uq_runtime_workflow_checkpoint_index",
        ),
        sa.UniqueConstraint(
            "workflow_run_id",
            "idempotency_key",
            name="uq_runtime_workflow_checkpoint_idempotency",
        ),
    )
    op.create_index(
        "ix_runtime_workflow_checkpoints_workflow_run_id",
        "runtime_workflow_checkpoints",
        ["workflow_run_id"],
    )
    op.create_index(
        "ix_runtime_workflow_checkpoints_run_created",
        "runtime_workflow_checkpoints",
        ["workflow_run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_workflow_checkpoints_run_created",
        table_name="runtime_workflow_checkpoints",
    )
    op.drop_index(
        "ix_runtime_workflow_checkpoints_workflow_run_id",
        table_name="runtime_workflow_checkpoints",
    )
    op.drop_table("runtime_workflow_checkpoints")

    op.drop_index(
        "ix_runtime_workflow_runs_user_type",
        table_name="runtime_workflow_runs",
    )
    op.drop_index(
        "ix_runtime_workflow_runs_user_status",
        table_name="runtime_workflow_runs",
    )
    op.drop_index(
        "ix_runtime_workflow_runs_thread_id",
        table_name="runtime_workflow_runs",
    )
    op.drop_index(
        "ix_runtime_workflow_runs_user_id",
        table_name="runtime_workflow_runs",
    )
    op.drop_table("runtime_workflow_runs")
