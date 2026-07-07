"""Retry caps for pending memory ops and thread-archival backoff state.

Revision ID: 022_retry_hygiene
Revises: 021_repair_profile_update_candidates
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "022_retry_hygiene"
down_revision = "021_repair_profile_update_candidates"
branch_labels = None
depends_on = None

TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True).with_variant(
    sa.TIMESTAMP(timezone=True), "sqlite"
)


def _existing_columns(inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    # Defensive like 021: a badly-stamped DB may be missing tables entirely
    # (they are then created from the models with these columns already
    # present), and re-runs must not re-add columns.
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("pending_memory_ops"):
        columns = _existing_columns(inspector, "pending_memory_ops")
        if "retry_count" not in columns:
            with op.batch_alter_table("pending_memory_ops") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "retry_count",
                        sa.Integer(),
                        nullable=False,
                        server_default=sa.text("0"),
                    )
                )

    if inspector.has_table("runtime_threads"):
        columns = _existing_columns(inspector, "runtime_threads")
        missing: list[sa.Column] = []
        if "archive_retry_count" not in columns:
            missing.append(
                sa.Column(
                    "archive_retry_count",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        if "archive_next_retry_at" not in columns:
            missing.append(
                sa.Column("archive_next_retry_at", TIMESTAMPTZ, nullable=True)
            )
        if "archive_failed" not in columns:
            missing.append(
                sa.Column(
                    "archive_failed",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                )
            )
        if missing:
            with op.batch_alter_table("runtime_threads") as batch_op:
                for column in missing:
                    batch_op.add_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("runtime_threads"):
        columns = _existing_columns(inspector, "runtime_threads")
        present = [
            name
            for name in ("archive_failed", "archive_next_retry_at", "archive_retry_count")
            if name in columns
        ]
        if present:
            with op.batch_alter_table("runtime_threads") as batch_op:
                for name in present:
                    batch_op.drop_column(name)

    if inspector.has_table("pending_memory_ops"):
        if "retry_count" in _existing_columns(inspector, "pending_memory_ops"):
            with op.batch_alter_table("pending_memory_ops") as batch_op:
                batch_op.drop_column("retry_count")
