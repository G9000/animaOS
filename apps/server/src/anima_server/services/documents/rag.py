from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeDocument, RuntimeDocumentChunk
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent.embeddings import generate_embedding
from anima_server.services.agent.pgvec_store import PgVecStore
from anima_server.services.agent.vector_store import VectorSearchResult
from anima_server.services.documents.indexing import EmbeddingFn, _run_embedding


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

    embed = embedding_fn or generate_embedding
    query_embedding = _run_embedding(embed, query)
    if not query_embedding:
        return []

    source_ids = None
    search_limit = _candidate_limit(limit)
    if allowed_document_ids is not None:
        source_ids = _live_document_chunk_ids(
            runtime_db,
            user_id=user_id,
            document_ids=allowed_document_ids,
        )
        if not source_ids:
            return []
        search_limit = limit

    vector_hits = PgVecStore(runtime_db).search_by_vector(
        user_id,
        query_embedding=query_embedding,
        limit=search_limit,
        source_types=["document_chunk"],
        source_ids=source_ids,
    )
    ranked_chunk_ids = _ranked_document_chunk_ids(vector_hits)
    if not ranked_chunk_ids:
        return []

    hydrated = _load_document_chunks(
        runtime_db,
        user_id=user_id,
        chunk_ids=ranked_chunk_ids,
        document_ids=allowed_document_ids,
    )

    results: list[DocumentRagResult] = []
    for hit in vector_hits:
        if hit.source_type != "document_chunk":
            continue
        pair = hydrated.get(hit.item_id)
        if pair is None:
            continue
        chunk, document = pair
        results.append(
            DocumentRagResult(
                chunk_id=chunk.id,
                document_id=document.id,
                filename=document.filename,
                content=chunk.content_text,
                similarity=hit.similarity,
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
    document_ids: set[int],
) -> list[int]:
    return list(
        runtime_db.scalars(
            select(RuntimeDocumentChunk.id)
            .join(RuntimeDocument, RuntimeDocumentChunk.document_id == RuntimeDocument.id)
            .where(
                RuntimeDocumentChunk.user_id == user_id,
                RuntimeDocument.user_id == user_id,
                RuntimeDocument.status == "indexed",
                RuntimeDocumentChunk.document_id.in_(document_ids),
            )
            .order_by(RuntimeDocumentChunk.id)
        ).all()
    )


def _ranked_document_chunk_ids(
    vector_hits: Sequence[VectorSearchResult],
) -> list[int]:
    ranked: list[int] = []
    seen: set[int] = set()
    for hit in vector_hits:
        if hit.source_type != "document_chunk":
            continue
        item_id = hit.item_id
        if item_id in seen:
            continue
        seen.add(item_id)
        ranked.append(item_id)
    return ranked


def _load_document_chunks(
    runtime_db: Session,
    *,
    user_id: int,
    chunk_ids: Sequence[int],
    document_ids: set[int] | None,
) -> dict[int, tuple[RuntimeDocumentChunk, RuntimeDocument]]:
    stmt = (
        select(RuntimeDocumentChunk, RuntimeDocument)
        .join(RuntimeDocument, RuntimeDocumentChunk.document_id == RuntimeDocument.id)
        .join(
            RuntimeEmbedding,
            (RuntimeEmbedding.user_id == RuntimeDocumentChunk.user_id)
            & (RuntimeEmbedding.source_type == "document_chunk")
            & (RuntimeEmbedding.source_id == RuntimeDocumentChunk.id)
            & (RuntimeEmbedding.content_hash == RuntimeDocumentChunk.content_hash),
        )
        .where(
            RuntimeDocumentChunk.id.in_(chunk_ids),
            RuntimeDocumentChunk.user_id == user_id,
            RuntimeDocument.user_id == user_id,
            RuntimeDocument.status == "indexed",
        )
    )
    if document_ids is not None:
        stmt = stmt.where(RuntimeDocumentChunk.document_id.in_(document_ids))

    return {
        chunk.id: (chunk, document)
        for chunk, document in runtime_db.execute(stmt).all()
    }
