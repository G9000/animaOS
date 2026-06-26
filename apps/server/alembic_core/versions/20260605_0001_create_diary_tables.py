"""create diary tables

Revision ID: 20260605_0001
Revises: 20260522_0001
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260605_0001"
down_revision = "20260522_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diary_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.String(length=10), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("mood", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=24), server_default=sa.text("'user'"), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_diary_entries_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diary_entries")),
    )
    op.create_index(
        "ix_diary_entries_user_created",
        "diary_entries",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_diary_entries_user_date",
        "diary_entries",
        ["user_id", "entry_date"],
    )

    op.create_table(
        "diary_attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["diary_entries.id"],
            name=op.f("fk_diary_attachments_entry_id_diary_entries"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_diary_attachments_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diary_attachments")),
        sa.UniqueConstraint("storage_path", name=op.f("uq_diary_attachments_storage_path")),
    )
    op.create_index(
        "ix_diary_attachments_user_entry",
        "diary_attachments",
        ["user_id", "entry_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_diary_attachments_user_entry", table_name="diary_attachments")
    op.drop_table("diary_attachments")
    op.drop_index("ix_diary_entries_user_date", table_name="diary_entries")
    op.drop_index("ix_diary_entries_user_created", table_name="diary_entries")
    op.drop_table("diary_entries")
