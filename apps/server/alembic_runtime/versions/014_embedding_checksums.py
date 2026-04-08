"""Add runtime embedding checksums.

Revision ID: 014_embedding_checksums
Revises: 013_retrieval_feedback_corrections
Create Date: 2026-04-08
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "014_embedding_checksums"
down_revision = "013_retrieval_feedback_corrections"
branch_labels = None
depends_on = None


def _parse_embedding(raw: Any) -> list[float] | None:
    payload = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    if not isinstance(payload, list) or not payload:
        return None

    normalized: list[float] = []
    for value in payload:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        normalized.append(numeric)
    return normalized


def _compute_embedding_checksum(raw: Any) -> str | None:
    embedding = _parse_embedding(raw)
    if embedding is None:
        return None
    payload = struct.pack("!I", len(embedding)) + struct.pack(f"!{len(embedding)}d", *embedding)
    return hashlib.sha256(payload).hexdigest()


def upgrade() -> None:
    op.add_column("embeddings", sa.Column("embedding_checksum", sa.String(length=64), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, embedding::text AS embedding_text FROM embeddings")).mappings()
    updates = []
    for row in rows:
        checksum = _compute_embedding_checksum(row["embedding_text"])
        if checksum is None:
            raise RuntimeError(f"Unable to compute checksum for runtime embedding row {row['id']}")
        updates.append({"id": row["id"], "embedding_checksum": checksum})

    if updates:
        bind.execute(
            sa.text("UPDATE embeddings SET embedding_checksum = :embedding_checksum WHERE id = :id"),
            updates,
        )

    op.alter_column("embeddings", "embedding_checksum", nullable=False)


def downgrade() -> None:
    op.drop_column("embeddings", "embedding_checksum")