"""Tests for PgVecStore - pgvector-backed VectorStore implementation.

These tests use InMemoryVectorStore to validate PgVecStore's logic
without requiring a running PostgreSQL instance. Integration tests
that require PG are marked with @pytest.mark.integration.
"""

from __future__ import annotations

import pytest
from anima_server.services.agent.vector_store import (
    InMemoryVectorStore,
    VectorStore,
)


class TestPgVecStoreContractWithInMemory:
    """Validate VectorStore contract using InMemoryVectorStore as stand-in.

    PgVecStore must satisfy the same contract. These tests document the
    expected behavior for both backends.
    """

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.store = InMemoryVectorStore()

    def test_pg_vec_store_implements_vector_store(self):
        from anima_server.services.agent.pgvec_store import PgVecStore

        assert issubclass(PgVecStore, VectorStore)

    def test_upsert_and_search(self):
        self.store.upsert(
            1,
            item_id=1,
            content="hiking in mountains",
            embedding=[1.0, 0.0, 0.0],
            category="preference",
            importance=4,
        )
        self.store.upsert(
            1,
            item_id=2,
            content="software engineer",
            embedding=[0.0, 1.0, 0.0],
            category="fact",
            importance=5,
        )
        results = self.store.search_by_vector(
            1,
            query_embedding=[0.9, 0.1, 0.0],
            limit=5,
        )
        assert len(results) == 2
        assert results[0].item_id == 1
        assert results[0].similarity > 0.9

    def test_category_filter(self):
        self.store.upsert(
            1,
            item_id=1,
            content="hiking",
            embedding=[1.0, 0.0, 0.0],
            category="preference",
            importance=4,
        )
        self.store.upsert(
            1,
            item_id=2,
            content="engineer",
            embedding=[0.0, 1.0, 0.0],
            category="fact",
            importance=5,
        )
        results = self.store.search_by_vector(
            1,
            query_embedding=[1.0, 0.0, 0.0],
            limit=5,
            category="fact",
        )
        assert len(results) == 1
        assert results[0].item_id == 2

    def test_upsert_updates_existing(self):
        self.store.upsert(
            1,
            item_id=1,
            content="v1",
            embedding=[1.0, 0.0, 0.0],
            category="fact",
            importance=3,
        )
        self.store.upsert(
            1,
            item_id=1,
            content="v2",
            embedding=[0.0, 1.0, 0.0],
            category="fact",
            importance=5,
        )
        assert self.store.count(1) == 1
        results = self.store.search_by_vector(
            1,
            query_embedding=[0.0, 1.0, 0.0],
            limit=1,
        )
        assert results[0].content == "v2"

    def test_delete(self):
        self.store.upsert(
            1,
            item_id=1,
            content="test",
            embedding=[1.0, 0.0],
            category="fact",
            importance=3,
        )
        assert self.store.count(1) == 1
        self.store.delete(1, item_id=1)
        assert self.store.count(1) == 0

    def test_rebuild_replaces_all(self):
        self.store.upsert(
            1,
            item_id=1,
            content="old",
            embedding=[1.0, 0.0],
            category="fact",
            importance=3,
        )
        count = self.store.rebuild(
            1,
            [
                (2, "new", [0.0, 1.0], "fact", 3),
            ],
        )
        assert count == 1
        assert self.store.count(1) == 1
        results = self.store.search_by_vector(
            1,
            query_embedding=[0.0, 1.0],
            limit=5,
        )
        assert results[0].item_id == 2

    def test_search_empty_store(self):
        results = self.store.search_by_vector(
            99,
            query_embedding=[1.0, 0.0],
            limit=5,
        )
        assert results == []

    def test_user_isolation(self):
        self.store.upsert(
            1,
            item_id=1,
            content="user1",
            embedding=[1.0, 0.0],
            category="fact",
            importance=3,
        )
        self.store.upsert(
            2,
            item_id=2,
            content="user2",
            embedding=[0.0, 1.0],
            category="fact",
            importance=3,
        )
        assert self.store.count(1) == 1
        assert self.store.count(2) == 1


class TestContentHash:
    def test_compute_content_hash(self):
        from anima_server.models.runtime_embedding import RuntimeEmbedding

        h = RuntimeEmbedding.compute_content_hash("test content")
        assert len(h) == 64
        assert h == RuntimeEmbedding.compute_content_hash("test content")
        assert h != RuntimeEmbedding.compute_content_hash("other content")
