from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from anima_server.config import settings
from anima_server.models.runtime import RuntimeDocument, RuntimeDocumentChunk
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent.bm25_index import BM25Index
from anima_server.services.agent.embeddings import (
    _reciprocal_rank_fusion,
    generate_embedding,
)
from anima_server.services.agent.pgvec_store import PgVecStore
from anima_server.services.agent.vector_store import VectorSearchResult
from anima_server.services.documents.indexing import (
    EmbeddingFn,
    _run_embedding,
    embed_document_chunks,
    get_unembedded_chunks,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DocumentRagResult:
    chunk_id: int
    document_id: int
    filename: str
    content: str
    similarity: float
    page_start: int | None
    page_end: int | None
    section_title: str | None


def search_document_chunks(
    runtime_db: Session,
    user_id: int,
    query: str,
    *,
    document_ids: Sequence[int] | None = None,
    limit: int = 8,
    embedding_fn: EmbeddingFn | None = None,
) -> list[DocumentRagResult]:
    if limit <= 0:
        return []

    allowed_document_ids = set(document_ids) if document_ids is not None else None
    if allowed_document_ids == set():
        return []

    source_ids: list[int] | None = None
    source_id_query: Select[tuple[int]] | None = None
    if allowed_document_ids is None:
        if not _has_live_document_chunks(runtime_db, user_id=user_id):
            return []
        source_id_query = _live_document_chunk_id_query(
            user_id=user_id,
            document_ids=None,
        )
    else:
        source_ids = _live_document_chunk_ids(
            runtime_db,
            user_id=user_id,
            document_ids=allowed_document_ids,
        )
        if not source_ids:
            return []

    search_limit = _candidate_limit(limit)
    if allowed_document_ids is not None:
        search_limit = limit
    if settings.retrieval_reranker != "off":
        # Over-fetch for the rerank stage; the cross-encoder picks top-k.
        search_limit = max(search_limit, settings.retrieval_rerank_candidates)

    embed = embedding_fn or generate_embedding
    query_embedding = _run_embedding(embed, query)

    # The lexical arm runs regardless of query-vector availability so exact
    # keyword searches keep working through embedding outages (scaffold
    # provider, missing keys, provider cooldown).
    dense_ranking: list[tuple[int, float]] = []
    if query_embedding:
        _repair_documents_missing_vectors_after_reset(
            runtime_db,
            user_id=user_id,
            document_ids=allowed_document_ids,
            embedding_fn=embed,
        )
        store = PgVecStore(runtime_db)
        if source_id_query is not None:
            vector_hits = store.search_by_vector(
                user_id,
                query_embedding=query_embedding,
                limit=search_limit,
                source_types=["document_chunk"],
                source_id_query=source_id_query,
            )
        else:
            vector_hits = store.search_by_vector(
                user_id,
                query_embedding=query_embedding,
                limit=search_limit,
                source_types=["document_chunk"],
                source_ids=source_ids,
            )
        dense_ranking = _dense_document_chunk_ranking(vector_hits)
    lexical_ranking = _lexical_document_chunk_ranking(
        runtime_db,
        user_id=user_id,
        document_ids=allowed_document_ids,
        query=query,
        limit=search_limit,
    )
    if lexical_ranking:
        # RRF ties (disjoint hits at equal ranks) are broken toward the
        # lexical arm explicitly — an exact-token BM25 hit must be able to
        # win the top slot over an unrelated dense hit at limit=1, and the
        # fusion backends (Python vs Rust) do not share tie ordering.
        fused = _reciprocal_rank_fusion(lexical_ranking, dense_ranking)
        lexical_rank_by_id = {
            chunk_id: rank for rank, (chunk_id, _score) in enumerate(lexical_ranking)
        }
        ranked_chunk_ids = [
            chunk_id
            for chunk_id, _score in sorted(
                fused,
                key=lambda pair: (
                    -pair[1],
                    lexical_rank_by_id.get(pair[0], len(lexical_rank_by_id) + 1),
                ),
            )
        ]
    else:
        ranked_chunk_ids = [chunk_id for chunk_id, _similarity in dense_ranking]
    if not ranked_chunk_ids:
        return []

    hydrated = _load_document_chunks(
        runtime_db,
        user_id=user_id,
        chunk_ids=ranked_chunk_ids,
        document_ids=allowed_document_ids,
        # The current-embedding join validates dense hits (and repair has
        # run when a query vector exists). Without a query vector every hit
        # is lexical — the text itself matched — so requiring an embedding
        # row would drop all results during an outage after a vector reset.
        require_current_embedding=bool(query_embedding),
    )

    if settings.retrieval_reranker != "off":
        from anima_server.services.documents.reranker import rerank_chunk_ids

        reranked = rerank_chunk_ids(
            query,
            [
                (chunk_id, hydrated[chunk_id][0].content_text)
                for chunk_id in ranked_chunk_ids
                if chunk_id in hydrated
            ],
        )
        if reranked is not None:
            ranked_chunk_ids = reranked

    similarity_by_chunk_id = dict(dense_ranking)
    results: list[DocumentRagResult] = []
    for chunk_id in ranked_chunk_ids:
        pair = hydrated.get(chunk_id)
        if pair is None:
            continue
        chunk, document = pair
        results.append(
            DocumentRagResult(
                chunk_id=chunk.id,
                document_id=document.id,
                filename=document.filename,
                content=chunk.content_text,
                similarity=similarity_by_chunk_id.get(chunk_id, 0.0),
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_title=chunk.section_title,
            )
        )
        if len(results) >= limit:
            break
    return results


def _candidate_limit(limit: int) -> int:
    return max(limit * 4, limit + 20)


def _live_document_chunk_ids(
    runtime_db: Session,
    *,
    user_id: int,
    document_ids: set[int] | None,
) -> list[int]:
    stmt = _live_document_chunk_id_query(
        user_id=user_id,
        document_ids=document_ids,
    ).order_by(RuntimeDocumentChunk.id)

    return list(runtime_db.scalars(stmt).all())


def _has_live_document_chunks(
    runtime_db: Session,
    *,
    user_id: int,
) -> bool:
    stmt = _live_document_chunk_id_query(
        user_id=user_id,
        document_ids=None,
    ).limit(1)
    return runtime_db.scalar(stmt) is not None


def _live_document_chunk_id_query(
    *,
    user_id: int,
    document_ids: set[int] | None,
) -> Select[tuple[int]]:
    stmt = (
        select(RuntimeDocumentChunk.id)
        .join(RuntimeDocument, RuntimeDocumentChunk.document_id == RuntimeDocument.id)
        .where(
            RuntimeDocumentChunk.user_id == user_id,
            RuntimeDocument.user_id == user_id,
            RuntimeDocument.status == "indexed",
        )
    )
    if document_ids is not None:
        stmt = stmt.where(RuntimeDocumentChunk.document_id.in_(document_ids))

    return stmt


def _repair_documents_missing_vectors_after_reset(
    runtime_db: Session,
    *,
    user_id: int,
    document_ids: set[int] | None,
    embedding_fn: EmbeddingFn,
) -> None:
    for document_id in _indexed_documents_without_current_vectors(
        runtime_db,
        user_id=user_id,
        document_ids=document_ids,
    ):
        embed_document_chunks(
            runtime_db,
            user_id=user_id,
            document_id=document_id,
            embedding_fn=embedding_fn,
        )
        if get_unembedded_chunks(
            runtime_db,
            user_id=user_id,
            document_id=document_id,
        ):
            _delete_document_chunk_vectors(
                runtime_db,
                user_id=user_id,
                document_id=document_id,
            )


def _indexed_documents_without_current_vectors(
    runtime_db: Session,
    *,
    user_id: int,
    document_ids: set[int] | None,
) -> list[int]:
    chunk_exists = (
        select(RuntimeDocumentChunk.id)
        .where(
            RuntimeDocumentChunk.document_id == RuntimeDocument.id,
            RuntimeDocumentChunk.user_id == RuntimeDocument.user_id,
        )
        .exists()
    )
    current_vector_exists = (
        select(RuntimeEmbedding.id)
        .join(
            RuntimeDocumentChunk,
            RuntimeEmbedding.source_id == RuntimeDocumentChunk.id,
        )
        .where(
            RuntimeDocumentChunk.document_id == RuntimeDocument.id,
            RuntimeDocumentChunk.user_id == RuntimeDocument.user_id,
            RuntimeEmbedding.user_id == RuntimeDocument.user_id,
            RuntimeEmbedding.source_type == "document_chunk",
            RuntimeEmbedding.content_hash == RuntimeDocumentChunk.content_hash,
        )
        .exists()
    )
    stmt = (
        select(RuntimeDocument.id)
        .where(
            RuntimeDocument.user_id == user_id,
            RuntimeDocument.status == "indexed",
            chunk_exists,
            ~current_vector_exists,
        )
        .order_by(RuntimeDocument.id)
    )
    if document_ids is not None:
        stmt = stmt.where(RuntimeDocument.id.in_(document_ids))

    return list(runtime_db.scalars(stmt).all())


def _delete_document_chunk_vectors(
    runtime_db: Session,
    *,
    user_id: int,
    document_id: int,
) -> None:
    chunk_ids = select(RuntimeDocumentChunk.id).where(
        RuntimeDocumentChunk.user_id == user_id,
        RuntimeDocumentChunk.document_id == document_id,
    )
    runtime_db.execute(
        delete(RuntimeEmbedding).where(
            RuntimeEmbedding.user_id == user_id,
            RuntimeEmbedding.source_type == "document_chunk",
            RuntimeEmbedding.source_id.in_(chunk_ids),
        )
    )
    runtime_db.flush()


def _dense_document_chunk_ranking(
    vector_hits: Sequence[VectorSearchResult],
) -> list[tuple[int, float]]:
    ranked: list[tuple[int, float]] = []
    seen: set[int] = set()
    for hit in vector_hits:
        if hit.source_type != "document_chunk":
            continue
        item_id = hit.item_id
        if item_id in seen:
            continue
        seen.add(item_id)
        ranked.append((item_id, hit.similarity))
    return ranked


def _lexical_document_chunk_ranking(
    runtime_db: Session,
    *,
    user_id: int,
    document_ids: set[int] | None,
    query: str,
    limit: int,
) -> list[tuple[int, float]]:
    """BM25 ranking over live document chunks; degrades to [] so search stays dense-only."""
    from anima_server.services.documents.contextual import chunk_index_text

    try:
        stmt = (
            select(RuntimeDocumentChunk)
            .join(RuntimeDocument, RuntimeDocumentChunk.document_id == RuntimeDocument.id)
            .where(
                RuntimeDocumentChunk.user_id == user_id,
                RuntimeDocument.user_id == user_id,
                RuntimeDocument.status == "indexed",
            )
        )
        if document_ids is not None:
            stmt = stmt.where(RuntimeDocumentChunk.document_id.in_(document_ids))
        chunks = list(runtime_db.scalars(stmt).all())
        if not chunks:
            return []
        index = BM25Index()
        # Contextual blurbs join the lexical index text but never the
        # evidence text surfaced to callers.
        index.build([(chunk.id, chunk_index_text(chunk)) for chunk in chunks])
        return index.search(query, limit=limit)
    except Exception:
        logger.debug(
            "Lexical document chunk ranking failed for user %s",
            user_id,
            exc_info=True,
        )
        return []


def _load_document_chunks(
    runtime_db: Session,
    *,
    user_id: int,
    chunk_ids: Sequence[int],
    document_ids: set[int] | None,
    require_current_embedding: bool = True,
) -> dict[int, tuple[RuntimeDocumentChunk, RuntimeDocument]]:
    stmt = (
        select(RuntimeDocumentChunk, RuntimeDocument)
        .join(RuntimeDocument, RuntimeDocumentChunk.document_id == RuntimeDocument.id)
        .where(
            RuntimeDocumentChunk.id.in_(chunk_ids),
            RuntimeDocumentChunk.user_id == user_id,
            RuntimeDocument.user_id == user_id,
            RuntimeDocument.status == "indexed",
        )
    )
    if require_current_embedding:
        stmt = stmt.join(
            RuntimeEmbedding,
            (RuntimeEmbedding.user_id == RuntimeDocumentChunk.user_id)
            & (RuntimeEmbedding.source_type == "document_chunk")
            & (RuntimeEmbedding.source_id == RuntimeDocumentChunk.id)
            & (RuntimeEmbedding.content_hash == RuntimeDocumentChunk.content_hash),
        )
    if document_ids is not None:
        stmt = stmt.where(RuntimeDocumentChunk.document_id.in_(document_ids))

    return {
        chunk.id: (chunk, document)
        for chunk, document in runtime_db.execute(stmt).all()
    }
