from __future__ import annotations

import asyncio
import inspect
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Thread

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import (
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeLink,
    RuntimeSource,
    RuntimeSourceSpan,
)
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent.bm25_index import BM25Index
from anima_server.services.agent.embeddings import (
    _reciprocal_rank_fusion,
    generate_embedding,
)
from anima_server.services.corefs.sealed_runtime import (
    load_runtime_embedding_vector,
    persist_runtime_embedding,
)

EmbeddingFn = Callable[[str], list[float] | None | Awaitable[list[float] | None]]

logger = logging.getLogger(__name__)


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
    query_embedding = _call_embedding_fn(embedding_fn or generate_embedding, query)
    if not query_embedding:
        return retrieve_knowledge_text(
            db,
            user_id=user_id,
            query=query,
            limit_concepts=limit_concepts,
            limit_spans=limit_spans,
        )

    concept_hits = _concept_hits(
        db,
        user_id=user_id,
        query=query,
        query_embedding=query_embedding,
        limit=limit_concepts,
    )
    evidence_hits = _span_hits(
        db,
        user_id=user_id,
        query=query,
        query_embedding=query_embedding,
        limit=limit_spans,
    )
    if not concept_hits or not evidence_hits:
        lowered = query.strip().lower()
        if lowered:
            if not concept_hits:
                concept_hits = _text_concept_hits(
                    db,
                    user_id=user_id,
                    lowered_query=lowered,
                    limit=limit_concepts,
                )
            if not evidence_hits:
                evidence_hits = _text_span_hits(
                    db,
                    user_id=user_id,
                    lowered_query=lowered,
                    limit=limit_spans,
                )
        elif not concept_hits and not evidence_hits:
            return KnowledgeRetrievalResult()
    return KnowledgeRetrievalResult(
        concepts=concept_hits,
        evidence_spans=evidence_hits,
        links=_links_for_concepts(db, user_id=user_id, concept_hits=concept_hits),
    )


def retrieve_knowledge_text(
    db: Session,
    *,
    user_id: int,
    query: str,
    limit_concepts: int = 5,
    limit_spans: int = 5,
) -> KnowledgeRetrievalResult:
    lowered = query.strip().lower()
    if not lowered:
        return KnowledgeRetrievalResult()
    concept_hits = _text_concept_hits(
        db,
        user_id=user_id,
        lowered_query=lowered,
        limit=limit_concepts,
    )
    evidence_hits = _text_span_hits(
        db,
        user_id=user_id,
        lowered_query=lowered,
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
    embedding = _call_embedding_fn(embedding_fn, text)
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
            embedding_checksum=None,
            embedding=None,
            content_preview="",
            category=category,
            importance=importance,
        )
    else:
        existing.content_hash = content_hash
        existing.category = category
        existing.importance = importance
        existing.updated_at = now
    persist_runtime_embedding(
        db,
        row=existing,
        owner_id=user_id,
        embedding=embedding,
        content_preview=text[:200],
    )
    return existing


def _call_embedding_fn(embedding_fn: EmbeddingFn, text: str) -> list[float] | None:
    result = embedding_fn(text)
    if inspect.isawaitable(result):
        return _run_awaitable_blocking(result)
    return result


def _run_awaitable_blocking(awaitable: Awaitable[list[float] | None]) -> list[float] | None:
    async def _await_result() -> list[float] | None:
        return await awaitable

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_result())

    result: dict[str, list[float] | None] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(_await_result())
        except BaseException as exc:  # pragma: no cover - propagated to caller
            error["value"] = exc

    thread = Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "value" in error:
        raise error["value"]
    return result.get("value")


def _concept_hits(
    db: Session,
    *,
    user_id: int,
    query: str,
    query_embedding: list[float],
    limit: int,
) -> list[KnowledgeConceptHit]:
    # The lexical corpus covers every active concept — a concept ingested
    # while embeddings were unavailable must stay keyword-searchable — while
    # the dense arm naturally only ranks embedded rows.
    all_concepts = list(
        db.scalars(
            select(RuntimeKnowledgeConcept).where(
                RuntimeKnowledgeConcept.user_id == user_id,
                RuntimeKnowledgeConcept.status == "active",
            )
        ).all()
    )
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
    concepts_by_id = {concept.id: concept for concept in all_concepts}
    dense_ranked = sorted(
        (
            (concept.id, _cosine(query_embedding, vector))
            for concept, embedding in rows
            if (
                vector := load_runtime_embedding_vector(
                    db,
                    owner_id=user_id,
                    source_type="knowledge_concept",
                    source_id=int(concept.id),
                    persisted_embedding=embedding.embedding,
                )
            )
            is not None
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    dense_ranked = [(item_id, score) for item_id, score in dense_ranked if score > 0]
    dense_scores = dict(dense_ranked)
    lexical_ranked = _lexical_ranking(
        [(concept.id, _concept_embedding_text(concept)) for concept in all_concepts],
        query=query,
        limit=limit * 4,
    )
    fused = (
        _reciprocal_rank_fusion(dense_ranked, lexical_ranked) if lexical_ranked else dense_ranked
    )
    hits: list[KnowledgeConceptHit] = []
    for concept_id, _fused_score in fused[:limit]:
        concept = concepts_by_id.get(concept_id)
        if concept is None:
            continue
        hits.append(
            KnowledgeConceptHit(
                concept_id=concept.id,
                title=concept.title,
                slug=concept.slug,
                concept_type=concept.concept_type,
                summary=concept.description or concept.body_markdown[:240],
                score=dense_scores.get(concept_id, 0.0),
            )
        )
    return hits


def _span_hits(
    db: Session,
    *,
    user_id: int,
    query: str,
    query_embedding: list[float],
    limit: int,
) -> list[KnowledgeEvidenceSpanHit]:
    # The lexical corpus covers every evidence span — a source ingested
    # while embeddings were unavailable must stay keyword-searchable. Section
    # spans are parent read units, not evidence, and stay excluded (they are
    # deliberately never embedded either).
    all_rows = list(
        db.execute(
            select(RuntimeSourceSpan, RuntimeSource)
            .join(RuntimeSource, RuntimeSourceSpan.source_id == RuntimeSource.id)
            .where(
                RuntimeSourceSpan.user_id == user_id,
                RuntimeSource.user_id == user_id,
                RuntimeSourceSpan.span_kind != "section",
            )
        ).all()
    )
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
    spans_by_id = {span.id: (span, source) for span, source in all_rows}
    dense_ranked = sorted(
        (
            (span.id, _cosine(query_embedding, vector))
            for span, _source, embedding in rows
            if (
                vector := load_runtime_embedding_vector(
                    db,
                    owner_id=user_id,
                    source_type="source_span",
                    source_id=int(span.id),
                    persisted_embedding=embedding.embedding,
                )
            )
            is not None
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    dense_ranked = [(item_id, score) for item_id, score in dense_ranked if score > 0]
    dense_scores = dict(dense_ranked)
    lexical_ranked = _lexical_ranking(
        [(span.id, span.content_text) for span, _source in all_rows],
        query=query,
        limit=limit * 4,
    )
    fused = (
        _reciprocal_rank_fusion(dense_ranked, lexical_ranked) if lexical_ranked else dense_ranked
    )
    hits: list[KnowledgeEvidenceSpanHit] = []
    for span_id, _fused_score in fused[:limit]:
        pair = spans_by_id.get(span_id)
        if pair is None:
            continue
        span, source = pair
        hits.append(
            KnowledgeEvidenceSpanHit(
                span_id=span.id,
                source_id=source.id,
                source_uri=source.source_uri,
                span_kind=span.span_kind,
                locator=span.locator_json,
                content_text=span.content_text,
                score=dense_scores.get(span_id, 0.0),
            )
        )
    return hits


def _lexical_ranking(
    documents: list[tuple[int, str]],
    *,
    query: str,
    limit: int,
) -> list[tuple[int, float]]:
    """BM25 ranking over (id, text) pairs; degrades to [] so retrieval stays dense-only."""
    if not documents or limit <= 0:
        return []
    try:
        index = BM25Index()
        index.build(documents)
        return index.search(query, limit=limit)
    except Exception:
        logger.debug("Lexical knowledge ranking failed", exc_info=True)
        return []


def _text_concept_hits(
    db: Session,
    *,
    user_id: int,
    lowered_query: str,
    limit: int,
) -> list[KnowledgeConceptHit]:
    if limit <= 0:
        return []
    concepts = list(
        db.scalars(
            select(RuntimeKnowledgeConcept)
            .where(
                RuntimeKnowledgeConcept.user_id == user_id,
                RuntimeKnowledgeConcept.status == "active",
            )
            .order_by(
                RuntimeKnowledgeConcept.updated_at.desc(),
                RuntimeKnowledgeConcept.id.desc(),
            )
        ).all()
    )
    return [
        KnowledgeConceptHit(
            concept_id=concept.id,
            title=concept.title,
            slug=concept.slug,
            concept_type=concept.concept_type,
            summary=concept.description or concept.body_markdown[:240],
            score=1.0,
        )
        for concept in concepts
        if _contains_text(
            lowered_query,
            concept.title,
            concept.description,
            concept.body_markdown,
            concept.slug,
            concept.concept_type,
        )
    ][:limit]


def _text_span_hits(
    db: Session,
    *,
    user_id: int,
    lowered_query: str,
    limit: int,
) -> list[KnowledgeEvidenceSpanHit]:
    if limit <= 0:
        return []
    rows = list(
        db.execute(
            select(RuntimeSourceSpan, RuntimeSource)
            .join(RuntimeSource, RuntimeSourceSpan.source_id == RuntimeSource.id)
            .where(
                RuntimeSourceSpan.user_id == user_id,
                RuntimeSource.user_id == user_id,
                # Section spans are parent read units, not evidence — the
                # hybrid path excludes them and the fallback must match.
                RuntimeSourceSpan.span_kind != "section",
            )
            .order_by(RuntimeSourceSpan.created_at.desc(), RuntimeSourceSpan.id.desc())
        ).all()
    )
    return [
        KnowledgeEvidenceSpanHit(
            span_id=span.id,
            source_id=source.id,
            source_uri=source.source_uri,
            span_kind=span.span_kind,
            locator=span.locator_json,
            content_text=span.content_text,
            score=1.0,
        )
        for span, source in rows
        if _contains_text(
            lowered_query,
            span.content_text,
            span.span_kind,
            source.title,
            source.source_uri,
        )
    ][:limit]


def _contains_text(lowered_query: str, *values: object) -> bool:
    return any(
        lowered_query in str(value).lower()
        for value in values
        if value is not None and str(value).strip()
    )


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
