from __future__ import annotations

from typing import Any

from anima_server.models.runtime import RuntimeDocument, RuntimeDocumentChunk
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent import pgvec_store as pgvec_module
from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
from anima_server.services.documents import (
    DocumentRegistration,
    ExtractedDocumentChunk,
    embed_document_chunks,
    get_unembedded_chunks,
    list_document_chunks,
    register_document,
    replace_document_chunks,
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
