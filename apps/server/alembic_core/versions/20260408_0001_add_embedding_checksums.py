"""add embedding checksums to cached embeddings

Revision ID: 20260408_0001
Revises: 20260402_0001
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

revision = "20260408_0001"
down_revision = "20260402_0001"
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
    with op.batch_alter_table("memory_items") as batch_op:
        batch_op.add_column(sa.Column("embedding_checksum", sa.String(length=64), nullable=True))

    with op.batch_alter_table("kg_entities") as batch_op:
        batch_op.add_column(sa.Column("embedding_checksum", sa.String(length=64), nullable=True))

    bind = op.get_bind()
    for table_name in ("memory_items", "kg_entities"):
        rows = bind.execute(
            sa.text(f"SELECT id, embedding_json FROM {table_name} WHERE embedding_json IS NOT NULL")
        ).mappings()
        updates = []
        for row in rows:
            checksum = _compute_embedding_checksum(row["embedding_json"])
            if checksum is not None:
                updates.append({"id": row["id"], "embedding_checksum": checksum})

        if updates:
            bind.execute(
                sa.text(
                    f"UPDATE {table_name} SET embedding_checksum = :embedding_checksum WHERE id = :id"
                ),
                updates,
            )


def downgrade() -> None:
    with op.batch_alter_table("kg_entities") as batch_op:
        batch_op.drop_column("embedding_checksum")

    with op.batch_alter_table("memory_items") as batch_op:
        batch_op.drop_column("embedding_checksum")