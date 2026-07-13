"""create diary folders and add folder_id to diary entries

Revision ID: 20260701_0002
Revises: 20260701_0001
Create Date: 2026-07-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260701_0002"
down_revision = "20260701_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diary_folders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_diary_folders_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_diary_folders")),
    )
    op.create_index("ix_diary_folders_user_id", "diary_folders", ["user_id"])

    with op.batch_alter_table("diary_entries") as batch_op:
        batch_op.add_column(sa.Column("folder_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_diary_entries_folder_id_diary_folders"),
            "diary_folders",
            ["folder_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("diary_entries") as batch_op:
        batch_op.drop_constraint(
            op.f("fk_diary_entries_folder_id_diary_folders"),
            type_="foreignkey",
        )
        batch_op.drop_column("folder_id")

    op.drop_index("ix_diary_folders_user_id", table_name="diary_folders")
    op.drop_table("diary_folders")
