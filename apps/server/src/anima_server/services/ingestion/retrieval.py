from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import (
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeLink,
    RuntimeSource,
    RuntimeSourceSpan,
)
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
from anima_server.services.agent.embeddings import generate_embedding

EmbeddingFn = Callable[[str], list[float] | None]


@dataclass(frozen=True, slots=True)
class KnowledgeConceptHit:
    concept_id: int
    title: str
    slug: str
    concept_type: str
    summary: str
    score: float


@dataclass(frozen=True, slots=True)
class KnowledgeEvidenceSpanHit:
    span_id: int
    source_id: int
    source_uri: str
    span_kind: str
    locator: dict[str, object]
    content_text: str
    score: float


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalResult:
    concepts: list[KnowledgeConceptHit] = field(default_factory=list)
    evidence_spans: list[KnowledgeEvidenceSpanHit] = field(default_factory=list)
    links: list[dict[str, object]] = field(default_factory=list)


def upsert_concept_embedding(
    db: Session,
    *,
    concept: RuntimeKnowledgeConcept,
    embedding_fn: EmbeddingFn | None = None,
) -> RuntimeEmbedding | None:
    text = _concept_embedding_text(concept)
    return _upsert_embedding(
        db,
        user_id=concept.user_id,
        source_type="knowledge_concept",
        source_id=concept.id,
        text=text,
        category="knowledge",
        importance=3,
        embedding_fn=embedding_fn or generate_embedding,
    )


def upsert_source_span_embedding(
    db: Session,
    *,
    span: RuntimeSourceSpan,
    embedding_fn: EmbeddingFn | None = None,
) -> RuntimeEmbedding | None:
    return _upsert_embedding(
        db,
        user_id=span.user_id,
        source_type="source_span",
        source_id=span.id,
        text=span.content_text,
        category="source",
        importance=2,
        embedding_fn=embedding_fn or generate_embedding,
    )


def retrieve_knowledge(
    db: Session,
    *,
    user_id: int,
    query: str,
    embedding_fn: EmbeddingFn | None = None,
    limit_concepts: int = 5,
    limit_spans: int = 5,
) -> KnowledgeRetrievalResult:
    query_embedding = (embedding_fn or generate_embedding)(query)
    if not query_embedding:
        return KnowledgeRetrievalResult()

    concept_hits = _concept_hits(
        db,
        user_id=user_id,
        query_embedding=query_embedding,
        limit=limit_concepts,
    )
    evidence_hits = _span_hits(
        db,
        user_id=user_id,
        query_embedding=query_embedding,
        limit=limit_spans,
    )
    return KnowledgeRetrievalResult(
        concepts=concept_hits,
        evidence_spans=evidence_hits,
        links=_links_for_concepts(db, user_id=user_id, concept_hits=concept_hits),
    )


def _upsert_embedding(
    db: Session,
    *,
    user_id: int,
    source_type: str,
    source_id: int,
    text: str,
    category: str,
    importance: int,
    embedding_fn: EmbeddingFn,
) -> RuntimeEmbedding | None:
    embedding = embedding_fn(text)
    if not embedding:
        return None
    content_hash = RuntimeEmbedding.compute_content_hash(text)
    existing = db.scalar(
        select(RuntimeEmbedding).where(
            RuntimeEmbedding.user_id == user_id,
            RuntimeEmbedding.source_type == source_type,
            RuntimeEmbedding.source_id == source_id,
        )
    )
    now = datetime.now(UTC)
    if existing is None:
        existing = RuntimeEmbedding(
            user_id=user_id,
            source_type=source_type,
            source_id=source_id,
            content_hash=content_hash,
            embedding_checksum=compute_embedding_checksum(embedding),
            embedding=embedding,
            content_preview=text[:200],
            category=category,
            importance=importance,
        )
    else:
        existing.content_hash = content_hash
        existing.embedding_checksum = compute_embedding_checksum(embedding)
        existing.embedding = embedding
        existing.content_preview = text[:200]
        existing.category = category
        existing.importance = importance
        existing.updated_at = now
    db.add(existing)
    db.flush()
    return existing


def _concept_hits(
    db: Session,
    *,
    user_id: int,
    query_embedding: list[float],
    limit: int,
) -> list[KnowledgeConceptHit]:
    rows = list(
        db.execute(
            select(RuntimeKnowledgeConcept, RuntimeEmbedding)
            .join(
                RuntimeEmbedding,
                (RuntimeEmbedding.user_id == RuntimeKnowledgeConcept.user_id)
                & (RuntimeEmbedding.source_type == "knowledge_concept")
                & (RuntimeEmbedding.source_id == RuntimeKnowledgeConcept.id),
            )
            .where(
                RuntimeKnowledgeConcept.user_id == user_id,
                RuntimeKnowledgeConcept.status == "active",
                RuntimeEmbedding.user_id == user_id,
            )
        ).all()
    )
    scored = [
        (
            _cosine(query_embedding, _vector_to_list(embedding.embedding)),
            concept,
        )
        for concept, embedding in rows
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        KnowledgeConceptHit(
            concept_id=concept.id,
            title=concept.title,
            slug=concept.slug,
            concept_type=concept.concept_type,
            summary=concept.description or concept.body_markdown[:240],
            score=score,
        )
        for score, concept in scored[:limit]
        if score > 0
    ]


def _span_hits(
    db: Session,
    *,
    user_id: int,
    query_embedding: list[float],
    limit: int,
) -> list[KnowledgeEvidenceSpanHit]:
    rows = list(
        db.execute(
            select(RuntimeSourceSpan, RuntimeSource, RuntimeEmbedding)
            .join(RuntimeSource, RuntimeSourceSpan.source_id == RuntimeSource.id)
            .join(
                RuntimeEmbedding,
                (RuntimeEmbedding.user_id == RuntimeSourceSpan.user_id)
                & (RuntimeEmbedding.source_type == "source_span")
                & (RuntimeEmbedding.source_id == RuntimeSourceSpan.id),
            )
            .where(
                RuntimeSourceSpan.user_id == user_id,
                RuntimeSource.user_id == user_id,
                RuntimeEmbedding.user_id == user_id,
            )
        ).all()
    )
    scored = [
        (
            _cosine(query_embedding, _vector_to_list(embedding.embedding)),
            span,
            source,
        )
        for span, source, embedding in rows
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        KnowledgeEvidenceSpanHit(
            span_id=span.id,
            source_id=source.id,
            source_uri=source.source_uri,
            span_kind=span.span_kind,
            locator=span.locator_json,
            content_text=span.content_text,
            score=score,
        )
        for score, span, source in scored[:limit]
        if score > 0
    ]


def _links_for_concepts(
    db: Session,
    *,
    user_id: int,
    concept_hits: list[KnowledgeConceptHit],
) -> list[dict[str, object]]:
    concept_ids = [hit.concept_id for hit in concept_hits]
    if not concept_ids:
        return []
    return [
        {
            "source_concept_id": link.source_concept_id,
            "target_concept_id": link.target_concept_id,
            "link_type": link.link_type,
            "metadata": link.metadata_json,
        }
        for link in db.scalars(
            select(RuntimeKnowledgeLink).where(
                RuntimeKnowledgeLink.user_id == user_id,
                RuntimeKnowledgeLink.source_concept_id.in_(concept_ids),
            )
        ).all()
    ]


def _concept_embedding_text(concept: RuntimeKnowledgeConcept) -> str:
    parts = [concept.title]
    if concept.description:
        parts.append(concept.description)
    parts.append(concept.body_markdown)
    return "\n\n".join(part for part in parts if part.strip())


def _vector_to_list(value: object) -> list[float]:
    if isinstance(value, list):
        return [float(item) for item in value]
    if isinstance(value, tuple):
        return [float(item) for item in value]
    return [float(item) for item in list(value)]  # type: ignore[arg-type]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)
