"""add user profile fields

Revision ID: 20260312_0002
Revises: 04d82bffa29f
Create Date: 2026-03-12 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260312_0002"
down_revision = "04d82bffa29f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("users", sa.Column("gender", sa.String(length=16), nullable=True))
    op.add_column("users", sa.Column("age", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("birthday", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("users", "birthday")
    op.drop_column("users", "age")
    op.drop_column("users", "gender")
