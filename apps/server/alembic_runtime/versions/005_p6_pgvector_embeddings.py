"""P6: pgvector extension + embeddings table + HNSW index

Revision ID: 005
Revises: 004
Create Date: 2026-03-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def _get_embedding_dim() -> int:
    """Read the configured embedding dimension, defaulting to 768."""
    try:
        from anima_server.config import settings
        return settings.agent_embedding_dim
    except Exception:
        return 768


def upgrade() -> None:
    dim = _get_embedding_dim()

    # Enable pgvector extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "embeddings",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_id", sa.BigInteger, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        # Vector column — raw SQL because Alembic doesn't natively support pgvector types.
        # Dimension is read from config (agent_embedding_dim, default 768).
        sa.Column("embedding", sa.LargeBinary, nullable=False),
        sa.Column("content_preview", sa.String(200), nullable=False, server_default=""),
        sa.Column("category", sa.String(24), nullable=False, server_default="fact"),
        sa.Column("importance", sa.Integer, nullable=False, server_default=sa.text("3")),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False, server_default=sa.func.now()),
    )

    # Replace the placeholder LargeBinary column with a real vector column.
    # Alembic's create_table doesn't support custom pgvector types, so we
    # drop the placeholder and add the actual vector column via raw SQL.
    op.drop_column("embeddings", "embedding")
    op.execute(f"ALTER TABLE embeddings ADD COLUMN embedding vector({dim}) NOT NULL")

    # Indexes
    op.create_index("ix_embeddings_user_id", "embeddings", ["user_id"])
    op.create_index(
        "ix_embeddings_user_source",
        "embeddings",
        ["user_id", "source_type", "source_id"],
        unique=True,
    )

    # HNSW approximate nearest neighbor index for cosine distance
    op.execute(
        "CREATE INDEX ix_embeddings_hnsw ON embeddings "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.drop_table("embeddings")
    op.execute("DROP EXTENSION IF EXISTS vector")
