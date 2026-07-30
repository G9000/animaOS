from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeDocumentChunk
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent.embeddings import generate_embedding
from anima_server.services.agent.pgvec_store import PgVecStore
from anima_server.services.corefs.sealed_runtime import (
    runtime_index_for_sensitive_write,
)
from anima_server.services.documents.store import (
    get_document_for_user,
    set_document_status,
)

EmbeddingResult = list[float] | None
EmbeddingFn = (
    Callable[[str], EmbeddingResult]
    | Callable[[str], Awaitable[EmbeddingResult]]
)


def get_unembedded_chunks(
    runtime_db: Session,
    *,
    user_id: int,
    document_id: int,
) -> list[RuntimeDocumentChunk]:
    document = get_document_for_user(
        runtime_db,
        user_id=user_id,
        document_id=document_id,
    )
    if document is None:
        return []

    rows = runtime_db.execute(
        select(
            RuntimeDocumentChunk,
            RuntimeEmbedding.id,
            RuntimeEmbedding.embedding,
        )
        .outerjoin(
            RuntimeEmbedding,
            and_(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == "document_chunk",
                RuntimeEmbedding.source_id == RuntimeDocumentChunk.id,
                RuntimeEmbedding.content_hash == RuntimeDocumentChunk.content_hash,
            ),
        )
        .where(
            RuntimeDocumentChunk.document_id == document_id,
            RuntimeDocumentChunk.user_id == user_id,
        )
        .order_by(RuntimeDocumentChunk.chunk_index)
    ).all()
    runtime_index = runtime_index_for_sensitive_write(
        runtime_db,
        user_id=user_id,
    )
    missing: list[RuntimeDocumentChunk] = []
    for chunk, embedding_id, persisted_embedding in rows:
        if runtime_index is None:
            has_current_vector = persisted_embedding is not None
        else:
            has_current_vector = (
                embedding_id is not None
                and runtime_index.runtime_embedding_vector(
                    source_type="document_chunk",
                    source_id=chunk.id,
                )
                is not None
            )
        if not has_current_vector:
            missing.append(chunk)
    return missing


def embed_document_chunks(
    runtime_db: Session,
    *,
    user_id: int,
    document_id: int,
    embedding_fn: EmbeddingFn | None = None,
) -> int:
    document = get_document_for_user(
        runtime_db,
        user_id=user_id,
        document_id=document_id,
    )
    if document is None:
        return 0

    chunks = get_unembedded_chunks(
        runtime_db,
        user_id=user_id,
        document_id=document_id,
    )
    has_current_chunks = (
        runtime_db.scalar(
            select(RuntimeDocumentChunk.id)
            .where(
                RuntimeDocumentChunk.document_id == document_id,
                RuntimeDocumentChunk.user_id == user_id,
            )
            .limit(1)
        )
        is not None
    )
    embed = embedding_fn or generate_embedding
    store = PgVecStore(runtime_db)
    indexed_count = 0
    skipped_missing_embedding = False

    from anima_server.services.documents.contextual import chunk_index_text

    for chunk in chunks:
        # Contextual blurbs steer the vector only; the stored content (and
        # its hash, which drives re-embed checks) stays the raw chunk text.
        embedding = _run_embedding(embed, chunk_index_text(chunk))
        if not embedding:
            skipped_missing_embedding = True
            continue

        store.upsert_source(
            user_id,
            source_type="document_chunk",
            source_id=chunk.id,
            content=chunk.content_text,
            embedding=embedding,
            category="document",
            importance=3,
        )
        indexed_count += 1

    if (
        has_current_chunks
        and not skipped_missing_embedding
        and indexed_count == len(chunks)
    ):
        set_document_status(
            runtime_db,
            document_id=document.id,
            status="indexed",
            indexed=True,
        )

    runtime_db.flush()
    return indexed_count


def _run_embedding(fn: EmbeddingFn, text: str) -> EmbeddingResult:
    result = fn(text)
    if inspect.isawaitable(result):
        return _run_awaitable(result)
    return result


def _run_awaitable(awaitable: Awaitable[EmbeddingResult]) -> EmbeddingResult:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    return _run_awaitable_in_thread(awaitable)


def _run_awaitable_in_thread(
    awaitable: Awaitable[EmbeddingResult],
) -> EmbeddingResult:
    result: EmbeddingResult = None
    error: BaseException | None = None

    def runner() -> None:
        nonlocal result, error
        try:
            result = asyncio.run(awaitable)
        except BaseException as exc:
            error = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()

    if error is not None:
        raise error
    return result
