"""Add salience metadata to runtime memory candidates.

Revision ID: 019_candidate_salience
Revises: 018_image_assets
Create Date: 2026-07-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "019_candidate_salience"
down_revision = "018_image_assets"
branch_labels = None
depends_on = None

JSON = postgresql.JSON(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    with op.batch_alter_table("memory_candidates") as batch_op:
        batch_op.add_column(sa.Column("salience_json", JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("memory_candidates") as batch_op:
        batch_op.drop_column("salience_json")
