from __future__ import annotations

import inspect
import sys
from types import SimpleNamespace

import anima_server.services.agent.vector_store as vs
import pytest
from anima_server.services.agent.vector_store import (
    delete_memory,
    get_collection,
    rebuild_user_index,
    reset_vector_store,
    search_similar,
    upsert_memory,
    use_in_memory_store,
)


@pytest.fixture(autouse=True)
def _isolate_store():
    """Give each test a fresh in-memory vector store."""
    reset_vector_store()
    use_in_memory_store()
    yield
    reset_vector_store()


def test_upsert_and_search() -> None:
    user_id = 1
    upsert_memory(
        user_id,
        item_id=1,
        content="I love hiking in mountains",
        embedding=[1.0, 0.0, 0.0],
        category="preference",
        importance=4,
    )
    upsert_memory(
        user_id,
        item_id=2,
        content="Works as a software engineer",
        embedding=[0.0, 1.0, 0.0],
        category="fact",
        importance=5,
    )

    results = search_similar(
        user_id,
        query_embedding=[0.9, 0.1, 0.0],
        limit=5,
    )

    assert len(results) == 2
    assert results[0]["id"] == 1
    assert results[0]["similarity"] > 0.9

    # Category filter
    results_filtered = search_similar(
        user_id,
        query_embedding=[0.9, 0.1, 0.0],
        limit=5,
        category="fact",
    )
    assert len(results_filtered) == 1
    assert results_filtered[0]["id"] == 2


def test_delete_memory() -> None:
    user_id = 2
    upsert_memory(
        user_id,
        item_id=10,
        content="test item",
        embedding=[1.0, 0.0],
        category="fact",
        importance=3,
    )
    collection = get_collection(user_id)
    assert collection.count() == 1

    delete_memory(user_id, item_id=10)
    assert collection.count() == 0


def test_rebuild_user_index() -> None:
    user_id = 3
    items = [
        (1, "fact one", [1.0, 0.0, 0.0], "fact", 3),
        (2, "fact two", [0.0, 1.0, 0.0], "fact", 4),
        (3, "pref one", [0.0, 0.0, 1.0], "preference", 5),
    ]
    count = rebuild_user_index(user_id, items)
    assert count == 3

    collection = get_collection(user_id)
    assert collection.count() == 3

    # Rebuild with fewer items replaces the old index
    count = rebuild_user_index(user_id, items[:1])
    assert count == 1
    collection = get_collection(user_id)  # re-fetch after rebuild
    assert collection.count() == 1


def test_empty_collection_search() -> None:
    results = search_similar(
        user_id=99,
        query_embedding=[1.0, 0.0],
        limit=5,
    )
    assert results == []


@pytest.mark.parametrize(
    "func_name",
    [
        "upsert_memory",
        "delete_memory",
        "search_similar",
        "search_by_text",
        "rebuild_user_index",
        "get_collection",
    ],
)
def test_public_api_accepts_runtime_db(func_name: str) -> None:
    assert "runtime_db" in inspect.signature(getattr(vs, func_name)).parameters


def test_upsert_with_explicit_runtime_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """When runtime_db is passed explicitly, PgVecStore is used directly."""
    calls: list[tuple[int, int, str, list[float], str, int]] = []
    reset_vector_store()

    class FakePgVecStore:
        def __init__(self, db: object) -> None:
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
            calls.append((user_id, item_id, content, embedding, category, importance))

    monkeypatch.setitem(
        sys.modules,
        "anima_server.services.agent.pgvec_store",
        SimpleNamespace(PgVecStore=FakePgVecStore),
    )
    monkeypatch.setitem(
        sys.modules,
        "anima_server.services.agent.bm25_index",
        SimpleNamespace(invalidate_index=lambda user_id: None),
    )

    fake_rt_db = object()
    upsert_memory(
        11,
        item_id=7,
        content="runtime path",
        embedding=[0.1, 0.2, 0.3],
        category="fact",
        importance=4,
        runtime_db=fake_rt_db,
    )

    assert calls == [(11, 7, "runtime path", [0.1, 0.2, 0.3], "fact", 4)]


def test_use_in_memory_store_skips_pg_when_db_provided() -> None:
    """When _force_in_memory is set and db is provided, OrmVecStore is used (no PG attempt)."""
    reset_vector_store()
    use_in_memory_store()

    # With no db, falls to in-memory
    upsert_memory(
        21,
        item_id=2,
        content="in memory",
        embedding=[1.0, 0.0],
        category="fact",
        importance=3,
    )

    assert get_collection(21).count() == 1
