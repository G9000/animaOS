from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import numpy as np
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.services.agent import bm25_index as bm25_module
from anima_server.services.agent import vector_store as vector_module
from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
from anima_server.services.agent.pgvec_store import PgVecStore
from anima_server.services.agent.vector_store import (
    InMemoryVectorStore,
    get_collection,
    reset_vector_store,
    search_by_text,
    search_similar,
    upsert_memory,
    use_in_memory_store,
)
from sqlalchemy import select
from sqlalchemy.dialects import postgresql


class FakeSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []
        self.scalar_statements: list[Any] = []
        self.flushed = False

    def execute(self, stmt: Any) -> Any:
        self.statements.append(stmt)
        return SimpleNamespace(all=lambda: [])

    def scalar(self, stmt: Any) -> int:
        self.scalar_statements.append(stmt)
        return 0

    def flush(self) -> None:
        self.flushed = True


def _compiled_params(stmt: Any) -> dict[str, Any]:
    return stmt.compile(dialect=postgresql.dialect()).params


def _compiled_sql(stmt: Any) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_pgvec_upsert_source_builds_source_aware_upsert() -> None:
    db = FakeSession()
    embedding = [0.1, 0.2, 0.3]
    runtime_embedding = np.array(embedding, dtype=np.float32).tolist()

    PgVecStore(db).upsert_source(
        7,
        source_type="document_chunk",
        source_id=42,
        content="document chunk content",
        embedding=embedding,
    )

    assert db.flushed is True
    params = _compiled_params(db.statements[0])
    assert params["user_id"] == 7
    assert params["source_type"] == "document_chunk"
    assert params["source_id"] == 42
    assert params["content_hash"] == hashlib.sha256(b"document chunk content").hexdigest()
    assert params["embedding_checksum"] == compute_embedding_checksum(runtime_embedding)
    assert params["embedding"] == runtime_embedding
    assert params["content_preview"] == "document chunk content"
    assert params["category"] == "document"
    assert params["importance"] == 3


def test_pgvec_memory_upsert_delegates_to_memory_source(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_upsert_source(self, user_id: int, **kwargs: Any) -> None:
        calls.append({"user_id": user_id, **kwargs})

    monkeypatch.setattr(PgVecStore, "upsert_source", fake_upsert_source)

    PgVecStore(FakeSession()).upsert(
        8,
        item_id=11,
        content="memory content",
        embedding=[1.0, 0.0],
        category="fact",
        importance=5,
    )

    assert calls == [
        {
            "user_id": 8,
            "source_type": "memory_item",
            "source_id": 11,
            "content": "memory content",
            "embedding": [1.0, 0.0],
            "category": "fact",
            "importance": 5,
        }
    ]


def test_in_memory_store_keeps_memory_and_document_sources_separate() -> None:
    store = InMemoryVectorStore()
    store.upsert(
        1,
        item_id=42,
        content="memory hiking",
        embedding=[1.0, 0.0],
        category="fact",
    )
    store.upsert_source(
        1,
        source_type="document_chunk",
        source_id=42,
        content="document vector retrieval",
        embedding=[0.0, 1.0],
    )

    document_results = store.search_by_vector(
        1,
        query_embedding=[0.0, 1.0],
        limit=5,
        source_types=["document_chunk"],
    )
    memory_results = store.search_by_vector(
        1,
        query_embedding=[1.0, 0.0],
        limit=5,
        source_types=["memory_item"],
    )

    assert [result.item_id for result in document_results] == [42]
    assert [result.source_type for result in document_results] == ["document_chunk"]
    assert [result.item_id for result in memory_results] == [42]
    assert [result.source_type for result in memory_results] == ["memory_item"]
    assert store.count(1) == 2


def test_source_filtered_search_excludes_memory_rows() -> None:
    store = InMemoryVectorStore()
    store.upsert(
        1,
        item_id=10,
        content="memory row should be excluded",
        embedding=[1.0, 0.0],
    )
    store.upsert_source(
        1,
        source_type="document_chunk",
        source_id=20,
        content="document row should match",
        embedding=[1.0, 0.0],
    )

    results = store.search_by_vector(
        1,
        query_embedding=[1.0, 0.0],
        limit=10,
        source_types=["document_chunk"],
    )

    assert [result.item_id for result in results] == [20]
    assert [result.source_type for result in results] == ["document_chunk"]


def test_source_specific_delete_removes_only_matching_source() -> None:
    store = InMemoryVectorStore()
    store.upsert(
        1,
        item_id=42,
        content="memory survives",
        embedding=[1.0, 0.0],
    )
    store.upsert_source(
        1,
        source_type="document_chunk",
        source_id=42,
        content="document removed",
        embedding=[0.0, 1.0],
    )

    store.delete_source(1, source_type="document_chunk", source_id=42)

    results = store.search_by_vector(1, query_embedding=[1.0, 0.0], limit=10)
    assert [(result.source_type, result.item_id) for result in results] == [
        ("memory_item", 42)
    ]


def test_public_memory_api_still_searches_memory_rows() -> None:
    reset_vector_store()
    use_in_memory_store()
    try:
        upsert_memory(
            3,
            item_id=5,
            content="memory compatibility",
            embedding=[1.0, 0.0],
            category="preference",
            importance=4,
        )

        assert search_similar(3, query_embedding=[1.0, 0.0], limit=5) == [
            {
                "id": 5,
                "content": "memory compatibility",
                "category": "preference",
                "importance": 4,
                "similarity": 1.0,
            }
        ]
    finally:
        reset_vector_store()


def test_search_similar_returns_only_memory_rows_when_documents_exist() -> None:
    reset_vector_store()
    use_in_memory_store()
    try:
        upsert_memory(
            6,
            item_id=10,
            content="memory compatibility",
            embedding=[1.0, 0.0],
        )
        store = vector_module._get_fallback_store()
        store.upsert_source(
            6,
            source_type="document_chunk",
            source_id=20,
            content="document should not leak",
            embedding=[1.0, 0.0],
        )

        assert search_similar(6, query_embedding=[1.0, 0.0], limit=10) == [
            {
                "id": 10,
                "content": "memory compatibility",
                "category": "fact",
                "importance": 3,
                "similarity": 1.0,
            }
        ]
    finally:
        reset_vector_store()


def test_public_search_by_text_returns_only_memory_rows_when_documents_exist() -> None:
    reset_vector_store()
    use_in_memory_store()
    try:
        upsert_memory(
            6,
            item_id=10,
            content="shared keyword memory",
            embedding=[1.0, 0.0],
        )
        store = vector_module._get_fallback_store()
        store.upsert_source(
            6,
            source_type="document_chunk",
            source_id=20,
            content="shared keyword document",
            embedding=[1.0, 0.0],
        )

        assert search_by_text(6, query_text="shared", limit=10) == [
            {
                "id": 10,
                "content": "shared keyword memory",
                "category": "fact",
                "importance": 3,
                "similarity": 0.3333,
            }
        ]
    finally:
        reset_vector_store()


def test_get_collection_count_returns_only_memory_rows_when_documents_exist() -> None:
    reset_vector_store()
    use_in_memory_store()
    try:
        upsert_memory(
            7,
            item_id=10,
            content="memory one",
            embedding=[1.0, 0.0],
        )
        store = vector_module._get_fallback_store()
        store.upsert_source(
            7,
            source_type="document_chunk",
            source_id=20,
            content="document one",
            embedding=[0.0, 1.0],
        )

        assert store.count(7) == 2
        assert get_collection(7).count() == 1
    finally:
        reset_vector_store()


def test_pgvec_delete_source_builds_source_specific_delete() -> None:
    db = FakeSession()

    PgVecStore(db).delete_source(9, source_type="document_chunk", source_id=77)

    assert db.flushed is True
    sql = _compiled_sql(db.statements[0])
    assert "embeddings.user_id = 9" in sql
    assert "embeddings.source_type = 'document_chunk'" in sql
    assert "embeddings.source_id = 77" in sql


def test_pgvec_count_source_filters_by_source_type() -> None:
    db = FakeSession()

    assert PgVecStore(db).count_source(12, source_type="memory_item") == 0

    sql = _compiled_sql(db.scalar_statements[0])
    assert "embeddings.user_id = 12" in sql
    assert "embeddings.source_type = 'memory_item'" in sql


def test_pgvec_search_source_types_adds_sql_filter() -> None:
    db = FakeSession()

    PgVecStore(db).search_by_vector(
        2,
        query_embedding=[1.0, 0.0],
        limit=5,
        source_types=["document_chunk"],
    )

    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "embeddings.user_id" in sql
    assert "embeddings.source_type IN" in sql
    assert 2 in compiled.params.values()
    assert ["document_chunk"] in compiled.params.values()


def test_pgvec_search_source_ids_adds_sql_filter() -> None:
    db = FakeSession()

    PgVecStore(db).search_by_vector(
        2,
        query_embedding=[1.0, 0.0],
        limit=5,
        source_types=["document_chunk"],
        source_ids=[10, 20],
    )

    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "embeddings.source_id IN" in sql
    assert [10, 20] in compiled.params.values()
    assert db.flushed is False


def test_pgvec_search_source_id_query_adds_sql_filter() -> None:
    db = FakeSession()
    source_query = select(RuntimeEmbedding.source_id).where(
        RuntimeEmbedding.category == "document"
    )

    PgVecStore(db).search_by_vector(
        2,
        query_embedding=[1.0, 0.0],
        limit=5,
        source_types=["document_chunk"],
        source_id_query=source_query,
    )

    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "embeddings.source_id IN (SELECT" in sql
    assert "embeddings.category =" in sql
    assert db.flushed is False


def test_bm25_runtime_embedding_fallback_filters_memory_items() -> None:
    db = FakeSession()

    def execute(stmt: Any) -> Any:
        db.statements.append(stmt)
        return SimpleNamespace(all=lambda: [(10, "memory preview")])

    db.execute = execute  # type: ignore[method-assign]

    docs = bm25_module._load_runtime_embedding_documents(4, runtime_db=db)

    assert docs == [(10, "memory preview")]
    sql = _compiled_sql(db.statements[0])
    assert "embeddings.user_id = 4" in sql
    assert "embeddings.source_type = 'memory_item'" in sql
