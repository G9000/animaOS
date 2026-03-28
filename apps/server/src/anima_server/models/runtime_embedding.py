"""pgvector-backed embedding storage on the Runtime (PostgreSQL) engine.

Each row stores a single embedding vector alongside metadata that enables
upsert-by-source, staleness detection, and BM25 index construction without
touching the encrypted soul database.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.config import settings
from anima_server.db.runtime_base import RuntimeBase

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)


def _vector_column() -> Any:
    """Return a pgvector Vector column with the configured dimension."""
    from pgvector.sqlalchemy import Vector

    return Vector(settings.agent_embedding_dim)


class RuntimeEmbedding(RuntimeBase):
    __tablename__ = "embeddings"
    __table_args__ = (
        Index(
            "ix_embeddings_user_source",
            "user_id",
            "source_type",
            "source_id",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )  # "memory_item" | "episode" | "entity"
    source_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )  # PK in the source table
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )  # SHA-256 of plaintext for staleness detection
    embedding: Mapped[Any] = mapped_column(
        _vector_column(),
        nullable=False,
    )
    content_preview: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        server_default=text("''"),
    )  # first 200 chars for debugging / BM25
    category: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        server_default=text("'fact'"),
    )
    importance: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("3"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )

    @staticmethod
    def compute_content_hash(plaintext: str) -> str:
        """SHA-256 hex digest for staleness detection."""
        return hashlib.sha256(plaintext.encode()).hexdigest()
