"""Create knowledge graph tables (kg_entities, kg_relations).

Revision ID: 20260319_0002
Revises: 20260319_0001
Create Date: 2026-03-19
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260319_0002"
down_revision = "20260319_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "kg_entities",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_normalized", sa.String(200), nullable=False),
        sa.Column(
            "entity_type",
            sa.String(50),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("mentions", sa.Integer, nullable=False, server_default="1"),
        sa.Column("embedding_json", sa.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "name_normalized", name="uq_kg_entities_user_name"
        ),
    )
    op.create_index(
        "ix_kg_entities_user_type", "kg_entities", ["user_id", "entity_type"]
    )

    op.create_table(
        "kg_relations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.Integer,
            sa.ForeignKey("kg_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "destination_id",
            sa.Integer,
            sa.ForeignKey("kg_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(100), nullable=False),
        sa.Column("mentions", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "source_memory_id",
            sa.Integer,
            sa.ForeignKey("memory_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_kg_relations_source", "kg_relations", ["source_id"])
    op.create_index("ix_kg_relations_dest", "kg_relations", ["destination_id"])


def downgrade():
    op.drop_table("kg_relations")
    op.drop_table("kg_entities")
