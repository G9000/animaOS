"""SQLite-compatible runtime vector store for Turso-style deployments."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent.embedding_integrity import (
    check_embedding,
)
from anima_server.services.agent.text_processing import unicode_lexical_tokens

from .vector_store import VectorSearchResult, VectorStore

logger = logging.getLogger(__name__)


def _coerce_embedding(payload: object) -> list[float] | None:
    if payload is None:
        return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return None
    if not isinstance(payload, list):
        return None
    try:
        return [float(value) for value in payload]
    except (TypeError, ValueError):
        return None


def _canonicalize_runtime_embedding(embedding: Sequence[float]) -> list[float]:
    return [float(value) for value in embedding]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _text_similarity(query_text: str, content: str) -> float:
    query_terms = set(unicode_lexical_tokens(query_text, min_word_chars=1))
    content_terms = set(unicode_lexical_tokens(content, min_word_chars=1))
    if not query_terms or not content_terms:
        return 0.0
    intersection = query_terms & content_terms
    union = query_terms | content_terms
    return len(intersection) / len(union)


class TursoVecStore(VectorStore):
    """Vector store backed by runtime DB JSON embeddings."""

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
        content_hash = __import__("hashlib").sha256(content.encode()).hexdigest()
        from anima_server.services.agent.embedding_integrity import compute_embedding_checksum

        embedding_checksum = compute_embedding_checksum(runtime_embedding)
        row = self._db.scalar(
            select(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == source_type,
                RuntimeEmbedding.source_id == source_id,
            )
        )
        if row is None:
            self._db.add(
                RuntimeEmbedding(
                    user_id=user_id,
                    source_type=source_type,
                    source_id=source_id,
                    content_hash=content_hash,
                    embedding_checksum=embedding_checksum,
                    embedding=runtime_embedding,
                    content_preview=content[:200],
                    category=category,
                    importance=importance,
                )
            )
        else:
            row.content_hash = content_hash
            row.embedding_checksum = embedding_checksum
            row.embedding = runtime_embedding
            row.content_preview = content[:200]
            row.category = category
            row.importance = importance
        self._db.flush()

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

        candidate_limit = max(limit * 2, limit + 5)
        stmt = (
            select(RuntimeEmbedding)
            .where(RuntimeEmbedding.user_id == user_id)
            .order_by(RuntimeEmbedding.id)
            .limit(candidate_limit)
        )
        if category is not None:
            stmt = stmt.where(RuntimeEmbedding.category == category)
        if source_types is not None:
            stmt = stmt.where(RuntimeEmbedding.source_type.in_(source_types))
        if source_ids is not None:
            stmt = stmt.where(RuntimeEmbedding.source_id.in_(source_ids))
        if source_id_query is not None:
            stmt = stmt.where(RuntimeEmbedding.source_id.in_(source_id_query))

        rows = self._db.scalars(stmt).all()
        repaired_count = 0
        invalid_count = 0
        scored: list[tuple[float, VectorSearchResult]] = []
        for row in rows:
            row_embedding = _coerce_embedding(row.embedding)
            if row_embedding is None:
                continue
            checked = check_embedding(row_embedding, row.embedding_checksum)
            if checked.status == "checksum_mismatch":
                if checked.actual_checksum is not None:
                    row.embedding_checksum = checked.actual_checksum
                    repaired_count += 1
                else:
                    invalid_count += 1
                    continue
            if checked.status in {"invalid", "missing_checksum"}:
                invalid_count += 1
                continue
            sim = _cosine_similarity(query_embedding, checked.embedding)
            scored.append(
                (
                    sim,
                    VectorSearchResult(
                        item_id=row.source_id,
                        content=row.content_preview,
                        category=row.category,
                        importance=row.importance,
                        similarity=round(sim, 4),
                        source_type=getattr(row, "source_type", "memory_item"),
                    ),
                )
            )

        scored.sort(key=lambda item: item[0], reverse=True)
        if repaired_count:
            self._db.flush()
            logger.info(
                "Repaired %d runtime embedding checksums in Turso fallback for user %d",
                repaired_count,
                user_id,
            )
        if invalid_count:
            logger.warning(
                "Skipped %d runtime embeddings for user %d due to invalid checksum state",
                invalid_count,
                user_id,
            )
        return [record for _, record in scored[:limit]]

    def search_by_text(
        self,
        user_id: int,
        *,
        query_text: str,
        limit: int = 10,
        category: str | None = None,
        source_types: Sequence[str] | None = None,
    ) -> list[VectorSearchResult]:
        if limit <= 0:
            return []

        stmt = (
            select(RuntimeEmbedding)
            .where(RuntimeEmbedding.user_id == user_id)
            .order_by(RuntimeEmbedding.id)
            .limit(max(limit * 2, limit + 5))
        )
        if category is not None:
            stmt = stmt.where(RuntimeEmbedding.category == category)
        if source_types is not None:
            stmt = stmt.where(RuntimeEmbedding.source_type.in_(source_types))

        rows = self._db.scalars(stmt).all()
        scored: list[tuple[float, VectorSearchResult]] = []
        for row in rows:
            sim = _text_similarity(query_text, row.content_preview)
            if sim <= 0:
                continue
            scored.append(
                (
                    sim,
                    VectorSearchResult(
                        item_id=row.source_id,
                        content=row.content_preview,
                        category=row.category,
                        importance=row.importance,
                        similarity=round(sim, 4),
                        source_type=getattr(row, "source_type", "memory_item"),
                    ),
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        return [record for _, record in scored[:limit]]

    def rebuild(
        self,
        user_id: int,
        items: list[tuple[int, str, list[float], str, int]],
    ) -> int:
        from anima_server.services.agent.embedding_integrity import compute_embedding_checksum

        self._db.execute(
            delete(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == "memory_item",
            )
        )
        count = 0
        for item_id, content, embedding, category, importance in items:
            runtime_embedding = _canonicalize_runtime_embedding(embedding)
            content_hash = __import__("hashlib").sha256(content.encode()).hexdigest()
            self._db.add(
                RuntimeEmbedding(
                    user_id=user_id,
                    source_type="memory_item",
                    source_id=item_id,
                    content_hash=content_hash,
                    embedding_checksum=compute_embedding_checksum(runtime_embedding),
                    embedding=runtime_embedding,
                    content_preview=content[:200],
                    category=category,
                    importance=importance,
                )
            )
            count += 1
        self._db.flush()
        return count

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
