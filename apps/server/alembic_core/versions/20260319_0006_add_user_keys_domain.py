"""Add domain column to user_keys for per-domain DEK management.

Revision ID: 20260319_0006
Revises: 20260319_0005
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260319_0006"
down_revision = "20260319_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_keys") as batch_op:
        batch_op.add_column(
            sa.Column(
                "domain",
                sa.String(64),
                nullable=False,
                server_default="memories",
            ),
        )
        batch_op.drop_constraint("uq_user_keys_user_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_user_keys_user_domain",
            ["user_id", "domain"],
        )


def downgrade() -> None:
    with op.batch_alter_table("user_keys") as batch_op:
        batch_op.drop_constraint("uq_user_keys_user_domain", type_="unique")
        batch_op.create_unique_constraint(
            "uq_user_keys_user_id",
            ["user_id"],
        )
        batch_op.drop_column("domain")
