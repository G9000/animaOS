"""Tests for Retrieval Phase 3: Storage Architecture.

Covers:
- 3.1 SqliteVecStore persistence + InMemoryVectorStore parity
- 3.2 Embedding cache (LRU + TTL)
- 3.3 Tag system (CRUD + filtered search)
- 3.4 Claims layer (upsert + supersede)
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.base import Base
from anima_server.models import MemoryClaim, MemoryClaimEvidence, MemoryItem, MemoryItemTag, User
from anima_server.services.agent.vector_store import (
    InMemoryVectorStore,
    SqliteVecStore,
)
import anima_server.services.agent.embeddings as emb
from anima_server.services.agent.memory_store import (
    add_memory_item,
    add_tags_to_item,
    get_all_tags,
    get_items_by_tags,
    store_memory_item,
)
from anima_server.services.agent.claims import (
    derive_canonical_key,
    get_active_claims,
    upsert_claim,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@contextmanager
def _db_session() -> Generator[Session, None, None]:
    engine: Engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )
    Base.metadata.create_all(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _make_user(db: Session) -> User:
    user = User(username="phase3_tester",
                display_name="Tester", password_hash="x")
    db.add(user)
    db.flush()
    return user


def _make_item(
    db: Session,
    user_id: int,
    content: str,
    category: str = "fact",
    importance: int = 3,
) -> MemoryItem:
    item = MemoryItem(
        user_id=user_id,
        content=content,
        category=category,
        importance=importance,
        source="test",
    )
    db.add(item)
    db.flush()
    return item


# ===========================================================================
# 3.1 — Vector Store Tests
# ===========================================================================


class TestSqliteVecStore:
    """SqliteVecStore: persistent, cosine similarity, CRUD."""

    def test_upsert_and_search(self, tmp_path: Path) -> None:
        store = SqliteVecStore(tmp_path / "vec.db")
        store.upsert(1, item_id=1, content="hiking", embedding=[
                     1.0, 0.0, 0.0], category="preference", importance=4)
        store.upsert(1, item_id=2, content="engineer", embedding=[
                     0.0, 1.0, 0.0], category="fact", importance=5)

        results = store.search_by_vector(
            1, query_embedding=[0.9, 0.1, 0.0], limit=5)
        assert len(results) == 2
        assert results[0].item_id == 1
        assert results[0].similarity > 0.8

    def test_category_filter(self, tmp_path: Path) -> None:
        store = SqliteVecStore(tmp_path / "vec.db")
        store.upsert(1, item_id=1, content="hiking", embedding=[
                     1.0, 0.0], category="preference")
        store.upsert(1, item_id=2, content="engineer",
                     embedding=[0.0, 1.0], category="fact")

        results = store.search_by_vector(
            1, query_embedding=[1.0, 0.0], limit=5, category="fact")
        assert len(results) == 1
        assert results[0].item_id == 2

    def test_delete(self, tmp_path: Path) -> None:
        store = SqliteVecStore(tmp_path / "vec.db")
        store.upsert(1, item_id=10, content="test", embedding=[1.0, 0.0])
        assert store.count(1) == 1
        store.delete(1, item_id=10)
        assert store.count(1) == 0

    def test_rebuild(self, tmp_path: Path) -> None:
        store = SqliteVecStore(tmp_path / "vec.db")
        items = [
            (1, "a", [1.0, 0.0], "fact", 3),
            (2, "b", [0.0, 1.0], "fact", 4),
        ]
        assert store.rebuild(1, items) == 2
        assert store.count(1) == 2
        # Rebuild with fewer items replaces
        assert store.rebuild(1, items[:1]) == 1
        assert store.count(1) == 1

    def test_persistence(self, tmp_path: Path) -> None:
        db_path = tmp_path / "vec.db"
        store1 = SqliteVecStore(db_path)
        store1.upsert(1, item_id=1, content="persisted",
                      embedding=[1.0, 0.0, 0.0])
        # Close connection without deleting the file
        store1._conn.close()
        store1._conn = None

        # Re-open same DB file: data should survive
        store2 = SqliteVecStore(db_path)
        assert store2.count(1) == 1
        results = store2.search_by_vector(
            1, query_embedding=[1.0, 0.0, 0.0], limit=1)
        assert results[0].content == "persisted"

    def test_text_search(self, tmp_path: Path) -> None:
        store = SqliteVecStore(tmp_path / "vec.db")
        store.upsert(
            1, item_id=1, content="I love hiking in mountains", embedding=[1.0, 0.0])
        store.upsert(
            1, item_id=2, content="software engineer at Google", embedding=[0.0, 1.0])

        results = store.search_by_text(
            1, query_text="hiking mountains", limit=5)
        assert len(results) >= 1
        assert results[0].item_id == 1

    def test_user_isolation(self, tmp_path: Path) -> None:
        store = SqliteVecStore(tmp_path / "vec.db")
        store.upsert(1, item_id=1, content="user1", embedding=[1.0, 0.0])
        store.upsert(2, item_id=2, content="user2", embedding=[0.0, 1.0])

        assert store.count(1) == 1
        assert store.count(2) == 1
        results = store.search_by_vector(
            1, query_embedding=[1.0, 0.0], limit=10)
        assert all(r.content == "user1" for r in results)


class TestInMemoryVectorStore:
    """InMemoryVectorStore: functional parity with SqliteVecStore."""

    def test_upsert_and_search(self) -> None:
        store = InMemoryVectorStore()
        store.upsert(1, item_id=1, content="hiking", embedding=[1.0, 0.0, 0.0])
        store.upsert(1, item_id=2, content="engineer",
                     embedding=[0.0, 1.0, 0.0])

        results = store.search_by_vector(
            1, query_embedding=[0.9, 0.1, 0.0], limit=5)
        assert len(results) == 2
        assert results[0].item_id == 1

    def test_delete(self) -> None:
        store = InMemoryVectorStore()
        store.upsert(1, item_id=10, content="test", embedding=[1.0])
        assert store.count(1) == 1
        store.delete(1, item_id=10)
        assert store.count(1) == 0

    def test_rebuild(self) -> None:
        store = InMemoryVectorStore()
        items = [(1, "a", [1.0], "fact", 3), (2, "b", [0.5], "fact", 4)]
        assert store.rebuild(1, items) == 2
        assert store.count(1) == 2

    def test_reset_clears_all(self) -> None:
        store = InMemoryVectorStore()
        store.upsert(1, item_id=1, content="a", embedding=[1.0])
        store.upsert(2, item_id=2, content="b", embedding=[0.5])
        store.reset()
        assert store.count(1) == 0
        assert store.count(2) == 0


# ===========================================================================
# 3.2 — Embedding Cache Tests
# ===========================================================================


class TestEmbeddingCache:
    """LRU embedding cache with TTL."""

    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        emb.clear_embedding_cache()
        yield
        emb.clear_embedding_cache()

    def test_put_and_get(self) -> None:
        key = "test_key_1"
        emb._cache_put(key, [1.0, 2.0, 3.0])
        result = emb._cache_get(key)
        assert result == [1.0, 2.0, 3.0]

    def test_miss(self) -> None:
        result = emb._cache_get("nonexistent")
        assert result is None

    def test_ttl_expiry(self) -> None:
        key = "ttl_test"
        emb._cache_put(key, [1.0])
        # Manually expire by backdating the timestamp
        with emb._cache_lock:
            vec, _ = emb._embedding_cache[key]
            emb._embedding_cache[key] = (
                vec, time.monotonic() - emb._CACHE_TTL_S - 1)
        result = emb._cache_get(key)
        assert result is None

    def test_lru_eviction(self) -> None:
        original_max = emb._CACHE_MAX_SIZE
        try:
            emb._CACHE_MAX_SIZE = 3
            emb._cache_put("a", [1.0])
            emb._cache_put("b", [2.0])
            emb._cache_put("c", [3.0])
            emb._cache_put("d", [4.0])  # should evict "a"
            assert emb._cache_get("a") is None
            assert emb._cache_get("d") == [4.0]
        finally:
            emb._CACHE_MAX_SIZE = original_max

    def test_stats(self) -> None:
        emb._cache_put("s", [1.0])
        emb._cache_get("s")  # hit
        emb._cache_get("missing")  # miss
        stats = emb.get_embedding_cache_stats()
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1
        assert stats["size"] >= 1

    def test_clear(self) -> None:
        emb._cache_put("x", [1.0])
        emb.clear_embedding_cache()
        assert emb._cache_get("x") is None
        stats = emb.get_embedding_cache_stats()
        assert stats["size"] == 0


# ===========================================================================
# 3.3 — Tag System Tests
# ===========================================================================


class TestTagSystem:
    """Tag CRUD + filtered search via junction table."""

    def test_add_memory_item_with_tags(self) -> None:
        with _db_session() as db:
            user = _make_user(db)
            item = add_memory_item(
                db, user_id=user.id, content="works at Google",
                category="fact", tags=["work", "career"],
            )
            assert item is not None
            assert set(item.tags_json) == {"work", "career"}
            # Junction table rows
            tags = db.query(MemoryItemTag).filter_by(item_id=item.id).all()
            assert {t.tag for t in tags} == {"work", "career"}

    def test_store_memory_item_with_tags(self) -> None:
        with _db_session() as db:
            user = _make_user(db)
            result = store_memory_item(
                db, user_id=user.id, content="loves hiking",
                category="preference", tags=["hobby", "outdoors"],
            )
            assert result.action == "added"
            assert result.item is not None
            assert result.item.tags_json == ["hobby", "outdoors"]

    def test_add_tags_to_item(self) -> None:
        with _db_session() as db:
            user = _make_user(db)
            item = _make_item(db, user.id, "test item")
            add_tags_to_item(db, item_id=item.id,
                             user_id=user.id, tags=["a", "b"])
            tags = db.query(MemoryItemTag).filter_by(item_id=item.id).all()
            assert {t.tag for t in tags} == {"a", "b"}

    def test_get_items_by_tags_any(self) -> None:
        with _db_session() as db:
            user = _make_user(db)
            i1 = _make_item(db, user.id, "item1")
            i2 = _make_item(db, user.id, "item2")
            i3 = _make_item(db, user.id, "item3")
            add_tags_to_item(db, item_id=i1.id, user_id=user.id, tags=["work"])
            add_tags_to_item(db, item_id=i2.id,
                             user_id=user.id, tags=["hobby"])
            add_tags_to_item(db, item_id=i3.id, user_id=user.id,
                             tags=["work", "hobby"])

            # "any" mode: items with either tag
            items = get_items_by_tags(db, user_id=user.id, tags=[
                "work"], match_mode="any")
            ids = {i.id for i in items}
            assert i1.id in ids
            assert i3.id in ids
            assert i2.id not in ids

    def test_get_items_by_tags_all(self) -> None:
        with _db_session() as db:
            user = _make_user(db)
            i1 = _make_item(db, user.id, "item1")
            i2 = _make_item(db, user.id, "item2")
            add_tags_to_item(db, item_id=i1.id, user_id=user.id,
                             tags=["work", "career"])
            add_tags_to_item(db, item_id=i2.id, user_id=user.id, tags=["work"])

            # "all" mode: items with all specified tags
            items = get_items_by_tags(db, user_id=user.id, tags=[
                "work", "career"], match_mode="all")
            ids = {i.id for i in items}
            assert i1.id in ids
            assert i2.id not in ids

    def test_get_all_tags(self) -> None:
        with _db_session() as db:
            user = _make_user(db)
            i1 = _make_item(db, user.id, "item1")
            i2 = _make_item(db, user.id, "item2")
            add_tags_to_item(db, item_id=i1.id, user_id=user.id,
                             tags=["alpha", "beta"])
            add_tags_to_item(db, item_id=i2.id, user_id=user.id,
                             tags=["beta", "gamma"])

            all_tags = get_all_tags(db, user_id=user.id)
            assert set(all_tags) == {"alpha", "beta", "gamma"}


# ===========================================================================
# 3.4 — Claims Layer Tests
# ===========================================================================


class TestClaimsLayer:
    """Structured claim upsert, supersede, and canonical key derivation."""

    def test_derive_canonical_key_fact(self) -> None:
        result = derive_canonical_key("works as a software engineer", "fact")
        assert result is not None
        ns, slot, polarity = result
        assert ns == "fact"
        assert slot == "occupation"
        assert polarity == "positive"

    def test_derive_canonical_key_preference(self) -> None:
        result = derive_canonical_key("likes coffee", "preference")
        assert result is not None
        ns, slot, polarity = result
        assert ns == "preference"
        assert slot == "likes"

    def test_derive_canonical_key_no_match(self) -> None:
        result = derive_canonical_key("random sentence here", "fact")
        assert result is None

    def test_upsert_new_claim(self) -> None:
        with _db_session() as db:
            user = _make_user(db)
            claim = upsert_claim(
                db, user_id=user.id, content="works as a doctor",
                category="fact", importance=5, extractor="llm",
                evidence_text="I'm a doctor",
            )
            assert claim is not None
            assert claim.canonical_key == "user:fact:occupation"
            assert claim.status == "active"
            assert claim.value_text == "works as a doctor"
            # Evidence row
            evidence = db.query(MemoryClaimEvidence).filter_by(
                claim_id=claim.id).all()
            assert len(evidence) == 1
            assert evidence[0].source_text == "I'm a doctor"

    def test_upsert_supersedes_existing(self) -> None:
        with _db_session() as db:
            user = _make_user(db)
            old = upsert_claim(
                db, user_id=user.id, content="works as a doctor",
                category="fact", importance=5,
            )
            new = upsert_claim(
                db, user_id=user.id, content="works as a lawyer",
                category="fact", importance=5,
            )
            db.refresh(old)
            assert old.status == "superseded"
            assert old.superseded_by_id == new.id
            assert new.status == "active"
            assert new.value_text == "works as a lawyer"

    def test_upsert_same_value_adds_evidence(self) -> None:
        with _db_session() as db:
            user = _make_user(db)
            c1 = upsert_claim(
                db, user_id=user.id, content="works as a doctor",
                category="fact", evidence_text="source1",
            )
            c2 = upsert_claim(
                db, user_id=user.id, content="works as a doctor",
                category="fact", evidence_text="source2",
            )
            # Same claim returned
            assert c1.id == c2.id
            evidence = db.query(MemoryClaimEvidence).filter_by(
                claim_id=c1.id).all()
            assert len(evidence) == 2

    def test_get_active_claims(self) -> None:
        with _db_session() as db:
            user = _make_user(db)
            upsert_claim(db, user_id=user.id,
                         content="works as a doctor", category="fact")
            upsert_claim(db, user_id=user.id,
                         content="lives in NYC", category="fact")
            upsert_claim(db, user_id=user.id,
                         content="likes coffee", category="preference")

            all_claims = get_active_claims(db, user_id=user.id)
            assert len(all_claims) == 3

            fact_claims = get_active_claims(
                db, user_id=user.id, namespace="fact")
            assert len(fact_claims) == 2

    def test_generic_claim_for_unmatched_content(self) -> None:
        with _db_session() as db:
            user = _make_user(db)
            claim = upsert_claim(
                db, user_id=user.id,
                content="has two cats named Luna and Moshi",
                category="fact",
            )
            assert claim is not None
            assert claim.canonical_key.startswith("user:fact:")
            assert claim.status == "active"
