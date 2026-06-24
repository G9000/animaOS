from __future__ import annotations

from typing import Any

from anima_server.models.runtime import RuntimeDocument, RuntimeDocumentChunk
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent import pgvec_store as pgvec_module
from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
from anima_server.services.agent.vector_store import VectorSearchResult
from anima_server.services.documents import (
    DocumentRegistration,
    ExtractedDocumentChunk,
    embed_document_chunks,
    get_unembedded_chunks,
    list_document_chunks,
    register_document,
    replace_document_chunks,
    search_document_chunks,
    set_document_status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

pytest_plugins = ("conftest_runtime",)

_TEST_EMBEDDING_DIM = 768


def _embedding(*values: float) -> list[float]:
    return [*values, *([0.0] * (_TEST_EMBEDDING_DIM - len(values)))]


def _registration(
    *,
    user_id: int = 1,
    filename: str = "notes.md",
    sha256: str = "d" * 64,
) -> DocumentRegistration:
    return DocumentRegistration(
        user_id=user_id,
        filename=filename,
        mime_type="text/markdown",
        storage_path=f".anima/documents/{user_id}/{filename}",
        sha256=sha256,
        size_bytes=512,
    )


def _document_with_chunks(
    runtime_db: Session,
    *,
    user_id: int = 1,
    chunks: list[str] | None = None,
) -> tuple[RuntimeDocument, list[RuntimeDocumentChunk]]:
    document = register_document(runtime_db, _registration(user_id=user_id))
    chunk_texts = ["alpha notes", "beta notes"] if chunks is None else chunks
    inserted = replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=[
            ExtractedDocumentChunk(chunk_index=index, content_text=content)
            for index, content in enumerate(chunk_texts)
        ],
    )
    return document, inserted


def _document_with_extracted_chunks(
    runtime_db: Session,
    *,
    user_id: int = 1,
    filename: str = "notes.md",
    sha256: str = "d" * 64,
    chunks: list[ExtractedDocumentChunk],
) -> tuple[RuntimeDocument, list[RuntimeDocumentChunk]]:
    document = register_document(
        runtime_db,
        _registration(user_id=user_id, filename=filename, sha256=sha256),
    )
    inserted = replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=chunks,
    )
    return document, inserted


def _patch_pgvec_upsert(monkeypatch: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_upsert_source(
        self: Any,
        user_id: int,
        *,
        source_type: str,
        source_id: int,
        content: str,
        embedding: list[float],
        category: str = "document",
        importance: int = 3,
    ) -> None:
        calls.append(
            {
                "user_id": user_id,
                "source_type": source_type,
                "source_id": source_id,
                "content": content,
                "embedding": embedding,
                "category": category,
                "importance": importance,
            }
        )
        row = self._db.scalar(
            select(RuntimeEmbedding).where(
                RuntimeEmbedding.user_id == user_id,
                RuntimeEmbedding.source_type == source_type,
                RuntimeEmbedding.source_id == source_id,
            )
        )
        if row is None:
            row = RuntimeEmbedding(
                user_id=user_id,
                source_type=source_type,
                source_id=source_id,
                content_hash=RuntimeEmbedding.compute_content_hash(content),
                embedding_checksum=compute_embedding_checksum(embedding),
                embedding=embedding,
                content_preview=content[:200],
                category=category,
                importance=importance,
            )
            self._db.add(row)
        else:
            row.content_hash = RuntimeEmbedding.compute_content_hash(content)
            row.embedding_checksum = compute_embedding_checksum(embedding)
            row.embedding = embedding
            row.content_preview = content[:200]
            row.category = category
            row.importance = importance
        self._db.flush()

    monkeypatch.setattr(pgvec_module.PgVecStore, "upsert_source", fake_upsert_source)
    return calls


def _embedding_rows(runtime_db: Session) -> list[RuntimeEmbedding]:
    return list(
        runtime_db.scalars(
            select(RuntimeEmbedding).order_by(RuntimeEmbedding.source_id)
        ).all()
    )


def _add_document_embedding(
    runtime_db: Session,
    chunk: RuntimeDocumentChunk,
    *,
    embedding: list[float] | None = None,
) -> RuntimeEmbedding:
    vector = embedding or _embedding(1.0)
    row = RuntimeEmbedding(
        user_id=chunk.user_id,
        source_type="document_chunk",
        source_id=chunk.id,
        content_hash=chunk.content_hash,
        embedding_checksum=compute_embedding_checksum(vector),
        embedding=vector,
        content_preview=chunk.content_text[:200],
        category="document",
        importance=3,
    )
    runtime_db.add(row)
    runtime_db.flush()
    return row


def _mark_document_indexed(runtime_db: Session, document: RuntimeDocument) -> None:
    set_document_status(
        runtime_db,
        document_id=document.id,
        status="indexed",
        indexed=True,
    )


def test_embed_document_chunks_indexes_chunks_as_document_sources(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    document, chunks = _document_with_chunks(runtime_db)

    indexed = embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=lambda text: _embedding(float(len(text)), 1.0),
    )

    rows = _embedding_rows(runtime_db)
    assert indexed == 2
    assert [row.source_type for row in rows] == ["document_chunk", "document_chunk"]
    assert [row.source_id for row in rows] == [chunks[0].id, chunks[1].id]
    assert [row.content_hash for row in rows] == [
        chunk.content_hash for chunk in chunks
    ]


def test_embed_document_chunks_skips_matching_existing_embeddings(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    document, _chunks = _document_with_chunks(runtime_db)
    calls: list[str] = []

    def embedding_fn(text: str) -> list[float]:
        calls.append(text)
        return _embedding(1.0, float(len(calls)))

    assert embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=embedding_fn,
    ) == 2
    assert embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=embedding_fn,
    ) == 0
    assert calls == ["alpha notes", "beta notes"]
    assert get_unembedded_chunks(runtime_db, user_id=1, document_id=document.id) == []


def test_embed_document_chunks_reindexes_changed_chunk_content(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    document, chunks = _document_with_chunks(runtime_db)
    calls: list[str] = []

    def embedding_fn(text: str) -> list[float]:
        calls.append(text)
        return _embedding(float(len(calls)), 0.5)

    assert embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=embedding_fn,
    ) == 2

    changed = runtime_db.get(RuntimeDocumentChunk, chunks[1].id)
    assert changed is not None
    changed.content_text = "beta notes updated"
    changed.content_hash = RuntimeEmbedding.compute_content_hash(changed.content_text)
    runtime_db.flush()

    assert embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=embedding_fn,
    ) == 1
    assert calls == ["alpha notes", "beta notes", "beta notes updated"]

    rows = _embedding_rows(runtime_db)
    assert [row.content_hash for row in rows] == [
        chunk.content_hash
        for chunk in list_document_chunks(runtime_db, document_id=document.id)
    ]


def test_embed_document_chunks_marks_document_indexed_when_embeddings_succeed(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    document, _chunks = _document_with_chunks(runtime_db)

    indexed = embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=lambda _text: _embedding(0.2, 0.8),
    )

    refreshed = runtime_db.get(RuntimeDocument, document.id)
    assert indexed == 2
    assert refreshed is not None
    assert refreshed.status == "indexed"
    assert refreshed.indexed_at is not None


def test_embed_document_chunks_does_not_mark_empty_document_indexed(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    document, chunks = _document_with_chunks(runtime_db, chunks=[])

    indexed = embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=lambda _text: _embedding(0.2, 0.8),
    )

    refreshed = runtime_db.get(RuntimeDocument, document.id)
    assert chunks == []
    assert indexed == 0
    assert refreshed is not None
    assert refreshed.status == "registered"
    assert refreshed.indexed_at is None


def test_embed_document_chunks_missing_document_returns_zero_without_embedding_call(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    calls: list[str] = []

    def embedding_fn(text: str) -> list[float]:
        calls.append(text)
        return _embedding(1.0)

    assert embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=999,
        embedding_fn=embedding_fn,
    ) == 0
    assert calls == []

    document, _chunks = _document_with_chunks(runtime_db, user_id=2)
    assert embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=embedding_fn,
    ) == 0
    assert calls == []


def test_embed_document_chunks_skips_none_embeddings_and_leaves_document_unindexed(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    document, _chunks = _document_with_chunks(runtime_db)
    calls: list[str] = []

    def embedding_fn(text: str) -> list[float] | None:
        calls.append(text)
        if text == "alpha notes":
            return None
        return _embedding(1.0, 0.0)

    indexed = embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=embedding_fn,
    )

    refreshed = runtime_db.get(RuntimeDocument, document.id)
    assert indexed == 1
    assert calls == ["alpha notes", "beta notes"]
    assert len(_embedding_rows(runtime_db)) == 1
    assert refreshed is not None
    assert refreshed.status == "registered"
    assert refreshed.indexed_at is None


def test_embed_document_chunks_accepts_async_embedding_function(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    document, _chunks = _document_with_chunks(runtime_db, chunks=["async notes"])

    async def embedding_fn(text: str) -> list[float]:
        return _embedding(float(len(text)), 2.0)

    indexed = embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=embedding_fn,
    )

    assert indexed == 1
    assert len(_embedding_rows(runtime_db)) == 1


def test_get_unembedded_chunks_returns_empty_for_missing_document(
    runtime_db: Session,
) -> None:
    assert get_unembedded_chunks(runtime_db, user_id=1, document_id=123) == []


def test_get_unembedded_chunks_orders_by_chunk_index(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    document, _chunks = _document_with_chunks(
        runtime_db,
        chunks=["first", "second", "third"],
    )
    calls: list[str] = []

    def embedding_fn(text: str) -> list[float]:
        calls.append(text)
        return _embedding(1.0)

    assert embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=embedding_fn,
    ) == 3

    middle = list_document_chunks(runtime_db, document_id=document.id)[1]
    middle.content_text = "second changed"
    middle.content_hash = RuntimeEmbedding.compute_content_hash(middle.content_text)
    runtime_db.flush()

    pending = get_unembedded_chunks(runtime_db, user_id=1, document_id=document.id)

    assert [chunk.chunk_index for chunk in pending] == [1]


def test_embed_document_chunks_does_not_mark_indexed_when_empty_embedding_is_returned(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    document, _chunks = _document_with_chunks(runtime_db, chunks=["empty vector"])

    indexed = embed_document_chunks(
        runtime_db,
        user_id=1,
        document_id=document.id,
        embedding_fn=lambda _text: [],
    )

    refreshed = runtime_db.get(RuntimeDocument, document.id)
    assert indexed == 0
    assert refreshed is not None
    assert refreshed.status == "registered"
    assert refreshed.indexed_at is None


def test_embed_document_chunks_uses_default_generator_when_no_embedding_fn(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _patch_pgvec_upsert(monkeypatch)
    document, _chunks = _document_with_chunks(runtime_db, chunks=["default generator"])
    calls: list[str] = []

    async def fake_generate_embedding(text: str) -> list[float]:
        calls.append(text)
        return _embedding(0.3, 0.7)

    monkeypatch.setattr(
        "anima_server.services.documents.indexing.generate_embedding",
        fake_generate_embedding,
    )

    indexed = embed_document_chunks(runtime_db, user_id=1, document_id=document.id)

    assert indexed == 1
    assert calls == ["default generator"]


def test_search_document_chunks_returns_document_hits_and_filters_memory_rows(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    document, chunks = _document_with_chunks(runtime_db)
    _add_document_embedding(runtime_db, chunks[1])
    _mark_document_indexed(runtime_db, document)
    calls: list[dict[str, Any]] = []

    def fake_search_by_vector(
        self: Any,
        user_id: int,
        *,
        query_embedding: list[float],
        limit: int = 10,
        category: str | None = None,
        source_types: list[str] | None = None,
        source_ids: list[int] | None = None,
        source_id_query: Any | None = None,
    ) -> list[VectorSearchResult]:
        calls.append(
            {
                "user_id": user_id,
                "query_embedding": query_embedding,
                "limit": limit,
                "category": category,
                "source_types": source_types,
                "source_ids": source_ids,
                "source_query_ids": (
                    list(runtime_db.scalars(source_id_query).all())
                    if source_id_query is not None
                    else None
                ),
            }
        )
        return [
            VectorSearchResult(
                item_id=chunks[1].id,
                content="beta preview",
                category="document",
                importance=3,
                similarity=0.92,
                source_type="document_chunk",
            ),
            VectorSearchResult(
                item_id=chunks[0].id,
                content="memory row with colliding id",
                category="fact",
                importance=5,
                similarity=0.91,
                source_type="memory_item",
            ),
        ]

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        fake_search_by_vector,
    )

    results = search_document_chunks(
        runtime_db,
        user_id=1,
        query="beta",
        limit=2,
        embedding_fn=lambda text: _embedding(float(len(text)), 1.0),
    )

    assert calls == [
        {
            "user_id": 1,
            "query_embedding": _embedding(4.0, 1.0),
            "limit": 22,
            "category": None,
            "source_types": ["document_chunk"],
            "source_ids": None,
            "source_query_ids": [chunk.id for chunk in chunks],
        }
    ]
    assert len(results) == 1
    assert results[0].chunk_id == chunks[1].id
    assert results[0].document_id == document.id
    assert results[0].filename == "notes.md"
    assert results[0].content == "beta notes"
    assert results[0].similarity == 0.92


def test_search_document_chunks_unfiltered_search_uses_db_side_source_filter(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    document, chunks = _document_with_chunks(runtime_db, chunks=["global match"])
    _add_document_embedding(runtime_db, chunks[0])
    _mark_document_indexed(runtime_db, document)
    calls: list[dict[str, Any]] = []

    def fake_search_by_vector(
        self: Any,
        user_id: int,
        *,
        query_embedding: list[float],
        limit: int = 10,
        category: str | None = None,
        source_types: list[str] | None = None,
        source_ids: list[int] | None = None,
        source_id_query: Any | None = None,
    ) -> list[VectorSearchResult]:
        calls.append(
            {
                "source_ids": source_ids,
                "source_query_ids": (
                    list(runtime_db.scalars(source_id_query).all())
                    if source_id_query is not None
                    else None
                ),
            }
        )
        return [
            VectorSearchResult(
                item_id=chunks[0].id,
                content="global match",
                category="document",
                importance=3,
                similarity=0.96,
                source_type="document_chunk",
            )
        ]

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        fake_search_by_vector,
    )

    results = search_document_chunks(
        runtime_db,
        user_id=1,
        query="global",
        embedding_fn=lambda _text: _embedding(1.0),
    )

    assert calls == [
        {
            "source_ids": None,
            "source_query_ids": [chunks[0].id],
        }
    ]
    assert [result.chunk_id for result in results] == [chunks[0].id]


def test_search_document_chunks_ignores_unindexed_document_hits(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _document, chunks = _document_with_chunks(runtime_db)
    _add_document_embedding(runtime_db, chunks[0])

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        lambda *_args, **_kwargs: [
            VectorSearchResult(
                item_id=chunks[0].id,
                content="partial preview",
                category="document",
                importance=3,
                similarity=0.97,
                source_type="document_chunk",
            )
        ],
    )

    assert (
        search_document_chunks(
            runtime_db,
            user_id=1,
            query="partial",
            embedding_fn=lambda _text: _embedding(1.0),
        )
        == []
    )


def test_search_document_chunks_filters_indexed_chunk_ids_before_unscoped_search(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    unindexed_document, unindexed_chunks = _document_with_extracted_chunks(
        runtime_db,
        filename="partial.pdf",
        sha256="6" * 64,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=index,
                content_text=f"partial chunk {index}",
            )
            for index in range(25)
        ],
    )
    indexed_document, indexed_chunks = _document_with_extracted_chunks(
        runtime_db,
        filename="complete.pdf",
        sha256="7" * 64,
        chunks=[ExtractedDocumentChunk(chunk_index=0, content_text="complete match")],
    )
    for chunk in [*unindexed_chunks, *indexed_chunks]:
        _add_document_embedding(runtime_db, chunk)
    _mark_document_indexed(runtime_db, indexed_document)
    search_calls: list[dict[str, Any]] = []
    candidates = [
        *[
            VectorSearchResult(
                item_id=chunk.id,
                content=chunk.content_text,
                category="document",
                importance=3,
                similarity=0.99 - (index / 1000),
                source_type="document_chunk",
            )
            for index, chunk in enumerate(unindexed_chunks)
        ],
        VectorSearchResult(
            item_id=indexed_chunks[0].id,
            content="complete match",
            category="document",
            importance=3,
            similarity=0.5,
            source_type="document_chunk",
        ),
    ]

    def fake_search_by_vector(
        self: Any,
        user_id: int,
        *,
        query_embedding: list[float],
        limit: int = 10,
        category: str | None = None,
        source_types: list[str] | None = None,
        source_ids: list[int] | None = None,
        source_id_query: Any | None = None,
    ) -> list[VectorSearchResult]:
        source_query_ids = (
            list(runtime_db.scalars(source_id_query).all())
            if source_id_query is not None
            else None
        )
        search_calls.append(
            {
                "limit": limit,
                "source_types": source_types,
                "source_ids": source_ids,
                "source_query_ids": source_query_ids,
            }
        )
        if source_query_ids is not None:
            allowed_ids = set(source_query_ids)
        elif source_ids is not None:
            allowed_ids = set(source_ids)
        else:
            allowed_ids = None
        return [
            candidate
            for candidate in candidates
            if allowed_ids is None or candidate.item_id in allowed_ids
        ][:limit]

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        fake_search_by_vector,
    )

    results = search_document_chunks(
        runtime_db,
        user_id=1,
        query="complete",
        limit=1,
        embedding_fn=lambda _text: _embedding(1.0),
    )

    assert unindexed_document.status == "registered"
    assert search_calls == [
        {
            "limit": 21,
            "source_types": ["document_chunk"],
            "source_ids": None,
            "source_query_ids": [chunk.id for chunk in indexed_chunks],
        }
    ]
    assert [result.document_id for result in results] == [indexed_document.id]
    assert [result.content for result in results] == ["complete match"]


def test_search_document_chunks_document_filter_excludes_unindexed_documents(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    document, chunks = _document_with_chunks(runtime_db)
    _add_document_embedding(runtime_db, chunks[0])

    def fail_search_by_vector(
        self: Any,
        *_args: Any,
        **_kwargs: Any,
    ) -> list[VectorSearchResult]:
        raise AssertionError("vector search should not run for unindexed documents")

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        fail_search_by_vector,
    )

    assert (
        search_document_chunks(
            runtime_db,
            user_id=1,
            query="partial",
            document_ids=[document.id],
            embedding_fn=lambda _text: _embedding(1.0),
        )
        == []
    )


def test_search_document_chunks_skips_embedding_when_no_indexed_chunks(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    document, _chunks = _document_with_chunks(runtime_db)
    embedding_calls: list[str] = []

    def fail_search_by_vector(
        self: Any,
        *_args: Any,
        **_kwargs: Any,
    ) -> list[VectorSearchResult]:
        raise AssertionError("vector search should not run without indexed chunks")

    def embedding_fn(text: str) -> list[float]:
        embedding_calls.append(text)
        return _embedding(1.0)

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        fail_search_by_vector,
    )

    assert (
        search_document_chunks(
            runtime_db,
            user_id=1,
            query="partial",
            document_ids=[document.id],
            embedding_fn=embedding_fn,
        )
        == []
    )
    assert embedding_calls == []


def test_search_document_chunks_document_filter_overfetches_to_fill_limit(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    allowed_document, allowed_chunks = _document_with_extracted_chunks(
        runtime_db,
        filename="allowed.md",
        sha256="e" * 64,
        chunks=[
            ExtractedDocumentChunk(chunk_index=0, content_text="allowed first"),
            ExtractedDocumentChunk(chunk_index=1, content_text="allowed second"),
        ],
    )
    _blocked_document, blocked_chunks = _document_with_extracted_chunks(
        runtime_db,
        filename="blocked.md",
        sha256="f" * 64,
        chunks=[
            ExtractedDocumentChunk(chunk_index=0, content_text="blocked first"),
            ExtractedDocumentChunk(chunk_index=1, content_text="blocked second"),
        ],
    )
    for chunk in allowed_chunks:
        _add_document_embedding(runtime_db, chunk)
    _mark_document_indexed(runtime_db, allowed_document)
    search_calls: list[dict[str, Any]] = []

    candidates = [
        VectorSearchResult(
            item_id=blocked_chunks[0].id,
            content="blocked first",
            category="document",
            importance=3,
            similarity=0.99,
            source_type="document_chunk",
        ),
        VectorSearchResult(
            item_id=blocked_chunks[1].id,
            content="blocked second",
            category="document",
            importance=3,
            similarity=0.98,
            source_type="document_chunk",
        ),
        VectorSearchResult(
            item_id=allowed_chunks[0].id,
            content="allowed first",
            category="document",
            importance=3,
            similarity=0.9,
            source_type="document_chunk",
        ),
        VectorSearchResult(
            item_id=allowed_chunks[1].id,
            content="allowed second",
            category="document",
            importance=3,
            similarity=0.89,
            source_type="document_chunk",
        ),
    ]

    def fake_search_by_vector(
        self: Any,
        user_id: int,
        *,
        query_embedding: list[float],
        limit: int = 10,
        category: str | None = None,
        source_types: list[str] | None = None,
        source_ids: list[int] | None = None,
    ) -> list[VectorSearchResult]:
        search_calls.append(
            {
                "limit": limit,
                "source_types": source_types,
                "source_ids": source_ids,
            }
        )
        allowed_ids = set(source_ids or [])
        return [
            candidate
            for candidate in candidates
            if not allowed_ids or candidate.item_id in allowed_ids
        ][:limit]

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        fake_search_by_vector,
    )

    results = search_document_chunks(
        runtime_db,
        user_id=1,
        query="allowed",
        document_ids=[allowed_document.id],
        limit=2,
        embedding_fn=lambda _text: _embedding(1.0, 0.0),
    )

    assert search_calls == [
        {
            "limit": 2,
            "source_types": ["document_chunk"],
            "source_ids": [chunk.id for chunk in allowed_chunks],
        }
    ]
    assert [result.chunk_id for result in results] == [
        allowed_chunks[0].id,
        allowed_chunks[1].id,
    ]


def test_search_document_chunks_accepts_specified_positional_call_shape(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    document, chunks = _document_with_chunks(runtime_db, chunks=["positional chunk"])
    _add_document_embedding(runtime_db, chunks[0])
    _mark_document_indexed(runtime_db, document)

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        lambda *_args, **_kwargs: [
            VectorSearchResult(
                item_id=chunks[0].id,
                content="positional preview",
                category="document",
                importance=3,
                similarity=0.77,
                source_type="document_chunk",
            )
        ],
    )

    results = search_document_chunks(
        runtime_db,
        1,
        "positional",
        embedding_fn=lambda _text: _embedding(1.0),
    )

    assert [result.chunk_id for result in results] == [chunks[0].id]


def test_search_document_chunks_empty_embedding_returns_empty_without_search(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    def fail_search_by_vector(self: Any, **kwargs: Any) -> list[VectorSearchResult]:
        raise AssertionError("vector search should not run without an embedding")

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        fail_search_by_vector,
    )

    assert (
        search_document_chunks(
            runtime_db,
            user_id=1,
            query="missing",
            embedding_fn=lambda _text: None,
        )
        == []
    )
    assert (
        search_document_chunks(
            runtime_db,
            user_id=1,
            query="missing",
            embedding_fn=lambda _text: [],
        )
        == []
    )


def test_search_document_chunks_includes_citation_metadata(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    document, chunks = _document_with_extracted_chunks(
        runtime_db,
        filename="manual.pdf",
        sha256="1" * 64,
        chunks=[
            ExtractedDocumentChunk(
                chunk_index=0,
                content_text="install guide",
                page_start=3,
                page_end=4,
                section_title="Installation",
            )
        ],
    )
    _add_document_embedding(runtime_db, chunks[0])
    _mark_document_indexed(runtime_db, document)

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        lambda *_args, **_kwargs: [
            VectorSearchResult(
                item_id=chunks[0].id,
                content="install preview",
                category="document",
                importance=3,
                similarity=0.88,
                source_type="document_chunk",
            )
        ],
    )

    results = search_document_chunks(
        runtime_db,
        user_id=1,
        query="install",
        embedding_fn=lambda _text: _embedding(1.0),
    )

    assert len(results) == 1
    assert results[0].document_id == document.id
    assert results[0].filename == "manual.pdf"
    assert results[0].page_start == 3
    assert results[0].page_end == 4
    assert results[0].section_title == "Installation"


def test_search_document_chunks_ignores_stale_embedding_content_hash(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    document, chunks = _document_with_extracted_chunks(
        runtime_db,
        filename="freshness.pdf",
        sha256="2" * 64,
        chunks=[
            ExtractedDocumentChunk(chunk_index=0, content_text="changed text"),
            ExtractedDocumentChunk(chunk_index=1, content_text="fresh text"),
        ],
    )
    embedding = _embedding(1.0)
    runtime_db.add_all(
        [
            RuntimeEmbedding(
                user_id=document.user_id,
                source_type="document_chunk",
                source_id=chunks[0].id,
                content_hash=RuntimeEmbedding.compute_content_hash("old text"),
                embedding_checksum=compute_embedding_checksum(embedding),
                embedding=embedding,
                content_preview="old text",
                category="document",
                importance=3,
            ),
            RuntimeEmbedding(
                user_id=document.user_id,
                source_type="document_chunk",
                source_id=chunks[1].id,
                content_hash=chunks[1].content_hash,
                embedding_checksum=compute_embedding_checksum(embedding),
                embedding=embedding,
                content_preview="fresh text",
                category="document",
                importance=3,
            ),
        ]
    )
    runtime_db.flush()
    _mark_document_indexed(runtime_db, document)

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        lambda *_args, **_kwargs: [
            VectorSearchResult(
                item_id=chunks[0].id,
                content="old stale vector",
                category="document",
                importance=3,
                similarity=0.99,
                source_type="document_chunk",
            ),
            VectorSearchResult(
                item_id=chunks[1].id,
                content="fresh vector",
                category="document",
                importance=3,
                similarity=0.88,
                source_type="document_chunk",
            ),
        ],
    )

    results = search_document_chunks(
        runtime_db,
        user_id=1,
        query="fresh",
        embedding_fn=lambda _text: embedding,
    )

    assert [result.chunk_id for result in results] == [chunks[1].id]
    assert [result.content for result in results] == ["fresh text"]


def test_search_document_chunks_ignores_stale_or_wrong_user_vector_rows(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    _other_document, other_chunks = _document_with_chunks(runtime_db, user_id=2)

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        lambda *_args, **_kwargs: [
            VectorSearchResult(
                item_id=999_999,
                content="stale chunk",
                category="document",
                importance=3,
                similarity=0.95,
                source_type="document_chunk",
            ),
            VectorSearchResult(
                item_id=other_chunks[0].id,
                content="other user chunk",
                category="document",
                importance=3,
                similarity=0.94,
                source_type="document_chunk",
            ),
        ],
    )

    assert (
        search_document_chunks(
            runtime_db,
            user_id=1,
            query="private",
            embedding_fn=lambda _text: _embedding(1.0),
        )
        == []
    )


def test_search_document_chunks_accepts_async_embedding_function(
    runtime_db: Session,
    monkeypatch: Any,
) -> None:
    document, chunks = _document_with_chunks(runtime_db, chunks=["async query"])
    _add_document_embedding(runtime_db, chunks[0])
    _mark_document_indexed(runtime_db, document)

    monkeypatch.setattr(
        pgvec_module.PgVecStore,
        "search_by_vector",
        lambda *_args, **_kwargs: [
            VectorSearchResult(
                item_id=chunks[0].id,
                content="async query",
                category="document",
                importance=3,
                similarity=0.91,
                source_type="document_chunk",
            )
        ],
    )

    async def embedding_fn(text: str) -> list[float]:
        return _embedding(float(len(text)))

    results = search_document_chunks(
        runtime_db,
        user_id=1,
        query="async",
        embedding_fn=embedding_fn,
    )

    assert [result.chunk_id for result in results] == [chunks[0].id]
