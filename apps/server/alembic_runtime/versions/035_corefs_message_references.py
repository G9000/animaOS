"""Add canonical CoreFS message references to Runtime metadata.

Revision ID: 035_corefs_message_references
Revises: 034_corefs_runtime_index
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "035_corefs_message_references"
down_revision = "034_corefs_runtime_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("runtime_messages"):
        # A repaired legacy Runtime may carry an over-advanced stamp while
        # missing pre-stamp tables. RuntimeBase creates the complete current
        # table after Alembic; this revision must not make that repair path
        # unrecoverable merely to add nullable projection columns.
        return
    columns = {column["name"] for column in inspector.get_columns("runtime_messages")}
    with op.batch_alter_table("runtime_messages") as batch_op:
        if "corefs_message_id" not in columns:
            batch_op.add_column(sa.Column("corefs_message_id", sa.String(26), nullable=True))
        if "corefs_event_id" not in columns:
            batch_op.add_column(sa.Column("corefs_event_id", sa.String(26), nullable=True))
        if "corefs_sequence_id" not in columns:
            batch_op.add_column(sa.Column("corefs_sequence_id", sa.Integer(), nullable=True))
    inspector = inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("runtime_messages")}
    if "ix_runtime_messages_corefs_message_id" not in indexes:
        op.create_index(
            "ix_runtime_messages_corefs_message_id",
            "runtime_messages",
            ["corefs_message_id"],
        )
    if "ux_runtime_messages_corefs_event_id" not in indexes:
        op.create_index(
            "ux_runtime_messages_corefs_event_id",
            "runtime_messages",
            ["corefs_event_id"],
            unique=True,
        )
    if "ix_runtime_messages_corefs_sequence_id" not in indexes:
        op.create_index(
            "ix_runtime_messages_corefs_sequence_id",
            "runtime_messages",
            ["corefs_sequence_id"],
        )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if not inspector.has_table("runtime_messages"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("runtime_messages")}
    if "ix_runtime_messages_corefs_sequence_id" in indexes:
        op.drop_index("ix_runtime_messages_corefs_sequence_id", table_name="runtime_messages")
    if "ux_runtime_messages_corefs_event_id" in indexes:
        op.drop_index("ux_runtime_messages_corefs_event_id", table_name="runtime_messages")
    if "ix_runtime_messages_corefs_message_id" in indexes:
        op.drop_index("ix_runtime_messages_corefs_message_id", table_name="runtime_messages")
    columns = {column["name"] for column in inspector.get_columns("runtime_messages")}
    with op.batch_alter_table("runtime_messages") as batch_op:
        if "corefs_sequence_id" in columns:
            batch_op.drop_column("corefs_sequence_id")
        if "corefs_event_id" in columns:
            batch_op.drop_column("corefs_event_id")
        if "corefs_message_id" in columns:
            batch_op.drop_column("corefs_message_id")
