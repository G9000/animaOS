from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeDocumentChunk
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent.embeddings import generate_embedding
from anima_server.services.agent.pgvec_store import PgVecStore
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

    matching_embedding = (
        select(RuntimeEmbedding.id)
        .where(
            RuntimeEmbedding.user_id == user_id,
            RuntimeEmbedding.source_type == "document_chunk",
            RuntimeEmbedding.source_id == RuntimeDocumentChunk.id,
            RuntimeEmbedding.content_hash == RuntimeDocumentChunk.content_hash,
        )
        .exists()
    )

    return list(
        runtime_db.scalars(
            select(RuntimeDocumentChunk)
            .where(
                RuntimeDocumentChunk.document_id == document_id,
                RuntimeDocumentChunk.user_id == user_id,
                ~matching_embedding,
            )
            .order_by(RuntimeDocumentChunk.chunk_index)
        ).all()
    )


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

    for chunk in chunks:
        embedding = _run_embedding(embed, chunk.content_text)
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
        and not get_unembedded_chunks(
            runtime_db,
            user_id=user_id,
            document_id=document_id,
        )
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
