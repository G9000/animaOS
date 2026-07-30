"""pgvector-backed VectorStore implementation.

Uses PostgreSQL's pgvector extension for O(log n) approximate nearest
neighbor search via HNSW indexes. Falls back gracefully if the runtime
PG session is unavailable.
"""

from __future__ import annotations

import hashlib
import logging
import struct
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent.embedding_integrity import (
    check_embedding,
    compute_embedding_checksum,
)
from anima_server.services.agent.vector_store import VectorSearchResult, VectorStore
from anima_server.services.corefs.sealed_runtime import seal_runtime_fields

logger = logging.getLogger(__name__)


def _canonicalize_runtime_embedding(embedding: Sequence[float]) -> list[float]:
    """Match pgvector's float4 storage before hashing or persisting values."""
    return [struct.unpack("!f", struct.pack("!f", float(value)))[0] for value in embedding]


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
        self.upsert_source(
            user_id,
            source_type="memory_item",
            source_id=item_id,
            content=content,
            embedding=embedding,
            category=category,
            importance=importance,
        )

    def upsert_source(
        self,
        user_id: int,
        *,
        source_type: str,
        source_id: int,
        content: str,
        embedding: list[float],
        category: str = "document",
        importance: int = 3,
    ) -> None:
        runtime_embedding = _canonicalize_runtime_embedding(embedding)
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        embedding_checksum = compute_embedding_checksum(runtime_embedding)
        stmt = pg_insert(RuntimeEmbedding).values(
            user_id=user_id,
            source_type=source_type,
            source_id=source_id,
            content_hash=content_hash,
            embedding_checksum=embedding_checksum,
            embedding=runtime_embedding,
            content_preview="",
            category=category,
            importance=importance,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "source_type", "source_id"],
            set_={
                "embedding": stmt.excluded.embedding,
                "content_hash": stmt.excluded.content_hash,
                "embedding_checksum": stmt.excluded.embedding_checksum,
                "content_preview": "",
                "category": stmt.excluded.category,
                "importance": stmt.excluded.importance,
                "updated_at": func.now(),
            },
        )
        self._db.execute(stmt)
        self._db.flush()
        stored = self._db.scalar(
            select(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == source_type,
                RuntimeEmbedding.source_id == source_id,
            )
        )
        if stored is None:
            raise RuntimeError("Runtime embedding upsert did not return a stored row")
        seal_runtime_fields(
            self._db,
            row=stored,
            row_type="runtime_embedding",
            owner_id=user_id,
            payload={"content_preview": content[:200]},
            placeholders={"content_preview": ""},
        )

    def delete(self, user_id: int, *, item_id: int) -> None:
        self.delete_source(user_id, source_type="memory_item", source_id=item_id)

    def delete_source(self, user_id: int, *, source_type: str, source_id: int) -> None:
        self._db.execute(
            delete(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == source_type,
                RuntimeEmbedding.source_id == source_id,
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
        source_types: Sequence[str] | None = None,
        source_ids: Sequence[int] | None = None,
        source_id_query: Select[tuple[int]] | None = None,
    ) -> list[VectorSearchResult]:
        if limit <= 0:
            return []
        if source_ids is not None and source_id_query is not None:
            raise ValueError("source_ids and source_id_query are mutually exclusive")
        if source_ids is not None and not source_ids:
            return []

        distance = RuntimeEmbedding.embedding.cosine_distance(query_embedding)
        base_stmt = (
            select(RuntimeEmbedding, (1 - distance).label("similarity"))
            .where(RuntimeEmbedding.user_id == user_id)
            .order_by(distance)
        )
        if category is not None:
            base_stmt = base_stmt.where(RuntimeEmbedding.category == category)
        if source_types is not None:
            base_stmt = base_stmt.where(RuntimeEmbedding.source_type.in_(source_types))
        if source_ids is not None:
            base_stmt = base_stmt.where(RuntimeEmbedding.source_id.in_(source_ids))
        if source_id_query is not None:
            base_stmt = base_stmt.where(RuntimeEmbedding.source_id.in_(source_id_query))

        # Checksum filtering happens *after* the ANN fetch, so a fixed
        # candidate cap can return fewer than `limit` valid rows while good
        # candidates sit deeper in the ANN order.  Expand the fetch (bounded)
        # until we collect `limit` valid rows or the pool is exhausted.
        candidate_limit = max(limit * 2, limit + 5)
        max_candidates = max(limit * 8, limit + 50)
        results: list[VectorSearchResult] = []
        repaired_checksum_count = 0
        invalid_checksum_count = 0
        while True:
            rows = self._db.execute(base_stmt.limit(candidate_limit)).all()

            results = []
            repaired_checksum_count = 0
            invalid_checksum_count = 0
            for row in rows:
                checked = check_embedding(
                    row.RuntimeEmbedding.embedding,
                    row.RuntimeEmbedding.embedding_checksum,
                )
                if checked.status == "checksum_mismatch":
                    if checked.actual_checksum is None:
                        invalid_checksum_count += 1
                        continue
                    row.RuntimeEmbedding.embedding_checksum = checked.actual_checksum
                    repaired_checksum_count += 1
                if checked.status in {"invalid", "missing_checksum"}:
                    invalid_checksum_count += 1
                    continue
                results.append(
                    VectorSearchResult(
                        item_id=row.RuntimeEmbedding.source_id,
                        content=row.RuntimeEmbedding.content_preview,
                        category=row.RuntimeEmbedding.category,
                        importance=row.RuntimeEmbedding.importance,
                        similarity=round(float(row.similarity), 4),
                        source_type=getattr(row.RuntimeEmbedding, "source_type", "memory_item"),
                    )
                )
                if len(results) >= limit:
                    break

            # Enough valid rows, the ANN pool is exhausted (fewer rows came
            # back than we asked for), or we've hit the expansion ceiling.
            if (
                len(results) >= limit
                or len(rows) < candidate_limit
                or candidate_limit >= max_candidates
            ):
                break
            candidate_limit = min(candidate_limit * 2, max_candidates)

        if repaired_checksum_count:
            self._db.flush()
            logger.info(
                "Repaired %d runtime embeddings for user %d by resyncing checksum to stored pgvector payload",
                repaired_checksum_count,
                user_id,
            )
        if invalid_checksum_count:
            logger.warning(
                "Skipped %d runtime embeddings for user %d due to missing or invalid checksum state",
                invalid_checksum_count,
                user_id,
            )
        return results

    def search_by_text(
        self,
        user_id: int,
        *,
        query_text: str,
        limit: int = 10,
        category: str | None = None,
        source_types: Sequence[str] | None = None,
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
                RuntimeEmbedding.source_type == "memory_item",
            )
        )
        for item_id, content, embedding, category, importance in items:
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            runtime_embedding = _canonicalize_runtime_embedding(embedding)
            stored = RuntimeEmbedding(
                user_id=user_id,
                source_type="memory_item",
                source_id=item_id,
                content_hash=content_hash,
                embedding_checksum=compute_embedding_checksum(runtime_embedding),
                embedding=runtime_embedding,
                content_preview="",
                category=category,
                importance=importance,
            )
            seal_runtime_fields(
                self._db,
                row=stored,
                row_type="runtime_embedding",
                owner_id=user_id,
                payload={"content_preview": content[:200]},
                placeholders={"content_preview": ""},
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

    def count_source(self, user_id: int, *, source_type: str) -> int:
        return (
            self._db.scalar(
                select(func.count())
                .select_from(RuntimeEmbedding)
                .where(
                    RuntimeEmbedding.user_id == user_id,
                    RuntimeEmbedding.source_type == source_type,
                )
            )
            or 0
        )

    def reset(self) -> None:
        self._db.execute(delete(RuntimeEmbedding))
        self._db.flush()
