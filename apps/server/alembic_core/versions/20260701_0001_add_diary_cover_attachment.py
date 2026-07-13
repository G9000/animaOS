"""add cover attachment id to diary entries

Revision ID: 20260701_0001
Revises: 20260626_0002
Create Date: 2026-07-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260701_0001"
down_revision = "20260626_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("diary_entries") as batch_op:
        batch_op.add_column(sa.Column("cover_attachment_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_diary_entries_cover_attachment_id_diary_attachments"),
            "diary_attachments",
            ["cover_attachment_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("diary_entries") as batch_op:
        batch_op.drop_constraint(
            op.f("fk_diary_entries_cover_attachment_id_diary_attachments"),
            type_="foreignkey",
        )
        batch_op.drop_column("cover_attachment_id")
