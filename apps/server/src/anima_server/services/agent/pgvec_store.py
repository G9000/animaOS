"""pgvector-backed VectorStore implementation.

Uses PostgreSQL's pgvector extension for O(log n) approximate nearest
neighbor search via HNSW indexes. Falls back gracefully if the runtime
PG session is unavailable.
"""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent.embedding_integrity import check_embedding, compute_embedding_checksum
from anima_server.services.agent.vector_store import VectorSearchResult, VectorStore

logger = logging.getLogger(__name__)


class PgVecStore(VectorStore):
    """Vector store backed by pgvector in the runtime PostgreSQL."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def upsert(
        self,
        user_id: int,
        *,
        item_id: int,
        content: str,
        embedding: list[float],
        category: str = "fact",
        importance: int = 3,
    ) -> None:
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        embedding_checksum = compute_embedding_checksum(embedding)
        stmt = pg_insert(RuntimeEmbedding).values(
            user_id=user_id,
            source_type="memory_item",
            source_id=item_id,
            content_hash=content_hash,
            embedding_checksum=embedding_checksum,
            embedding=embedding,
            content_preview=content[:200],
            category=category,
            importance=importance,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "source_type", "source_id"],
            set_={
                "embedding": stmt.excluded.embedding,
                "content_hash": stmt.excluded.content_hash,
                "embedding_checksum": stmt.excluded.embedding_checksum,
                "content_preview": stmt.excluded.content_preview,
                "category": stmt.excluded.category,
                "importance": stmt.excluded.importance,
                "updated_at": func.now(),
            },
        )
        self._db.execute(stmt)
        self._db.flush()

    def delete(self, user_id: int, *, item_id: int) -> None:
        self._db.execute(
            delete(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == "memory_item",
                RuntimeEmbedding.source_id == item_id,
            )
        )
        self._db.flush()

    def search_by_vector(
        self,
        user_id: int,
        *,
        query_embedding: list[float],
        limit: int = 10,
        category: str | None = None,
    ) -> list[VectorSearchResult]:
        if limit <= 0:
            return []

        distance = RuntimeEmbedding.embedding.cosine_distance(query_embedding)
        candidate_limit = max(limit * 2, limit + 5)
        stmt = (
            select(RuntimeEmbedding, (1 - distance).label("similarity"))
            .where(RuntimeEmbedding.user_id == user_id)
            .order_by(distance)
            .limit(candidate_limit)
        )
        if category is not None:
            stmt = stmt.where(RuntimeEmbedding.category == category)
        rows = self._db.execute(stmt).all()

        results: list[VectorSearchResult] = []
        for row in rows:
            checked = check_embedding(
                row.RuntimeEmbedding.embedding,
                row.RuntimeEmbedding.embedding_checksum,
            )
            if checked.status == "checksum_mismatch":
                logger.warning(
                    "Skipping runtime embedding %s for user %d due to checksum mismatch",
                    row.RuntimeEmbedding.id,
                    user_id,
                )
                continue
            if checked.status in {"invalid", "missing_checksum"}:
                logger.warning(
                    "Skipping runtime embedding %s for user %d due to missing or invalid checksum state",
                    row.RuntimeEmbedding.id,
                    user_id,
                )
                continue
            results.append(
                VectorSearchResult(
                    item_id=row.RuntimeEmbedding.source_id,
                    content=row.RuntimeEmbedding.content_preview,
                    category=row.RuntimeEmbedding.category,
                    importance=row.RuntimeEmbedding.importance,
                    similarity=round(float(row.similarity), 4),
                )
            )
            if len(results) >= limit:
                break
        return results

    def search_by_text(
        self,
        user_id: int,
        *,
        query_text: str,
        limit: int = 10,
        category: str | None = None,
    ) -> list[VectorSearchResult]:
        return []

    def rebuild(
        self,
        user_id: int,
        items: list[tuple[int, str, list[float], str, int]],
    ) -> int:
        self._db.execute(
            delete(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
            )
        )
        for item_id, content, embedding, category, importance in items:
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            self._db.add(
                RuntimeEmbedding(
                    user_id=user_id,
                    source_type="memory_item",
                    source_id=item_id,
                    content_hash=content_hash,
                    embedding_checksum=compute_embedding_checksum(embedding),
                    embedding=embedding,
                    content_preview=content[:200],
                    category=category,
                    importance=importance,
                )
            )
        self._db.flush()
        return len(items)

    def count(self, user_id: int) -> int:
        return (
            self._db.scalar(
                select(func.count())
                .select_from(RuntimeEmbedding)
                .where(RuntimeEmbedding.user_id == user_id)
            )
            or 0
        )

    def reset(self) -> None:
        self._db.execute(delete(RuntimeEmbedding))
        self._db.flush()
