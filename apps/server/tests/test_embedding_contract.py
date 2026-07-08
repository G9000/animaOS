"""ARH-009: persisted embedding contract and derived-store consistency.

Switching embedding models used to silently kill semantic search: the
pgvector column stayed at the old dimension, every query raised into a
swallowed except, and retrieval degraded to keyword-only forever with no
signal and no recovery path.
"""

from __future__ import annotations

import logging

import pytest
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import MemoryItem, User
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.models.runtime_memory import EmbeddingConfig
from anima_server.services.agent import embedding_contract
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture(autouse=True)
def _fresh_contract_cache():
    embedding_contract.reset_contract_cache()
    yield
    embedding_contract.reset_contract_cache()


@pytest.fixture()
def rt_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    RuntimeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


@pytest.fixture()
def soul_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        db.add(User(username="embed", password_hash="x", display_name="E"))
        db.commit()
    yield factory
    engine.dispose()


def _embedding(dim: int = 768) -> list[float]:
    return [0.1] * dim


class TestContractCheck:
    def test_first_use_adopts_the_active_pair(self, rt_factory) -> None:
        assert (
            embedding_contract.check_embedding_contract(
                model="nomic-embed-text", dim=768, runtime_db_factory=rt_factory
            )
            is True
        )
        with rt_factory() as rt_db:
            row = rt_db.scalar(select(EmbeddingConfig))
        assert row.embedding_model == "nomic-embed-text"
        assert row.embedding_dim == 768
        assert row.reembed_required is False

    def test_matching_pair_passes(self, rt_factory) -> None:
        embedding_contract.check_embedding_contract(
            model="nomic-embed-text", dim=768, runtime_db_factory=rt_factory
        )
        assert (
            embedding_contract.check_embedding_contract(
                model="nomic-embed-text", dim=768, runtime_db_factory=rt_factory
            )
            is True
        )
        assert embedding_contract.is_reembed_required(runtime_db_factory=rt_factory) is False

    def test_model_switch_is_loud_and_persisted(
        self, rt_factory, caplog: pytest.LogCaptureFixture
    ) -> None:
        embedding_contract.check_embedding_contract(
            model="nomic-embed-text", dim=768, runtime_db_factory=rt_factory
        )
        with caplog.at_level(logging.ERROR, logger="anima.runtime.degraded"):
            result = embedding_contract.check_embedding_contract(
                model="mxbai-embed-large", dim=1024, runtime_db_factory=rt_factory
            )
        assert result is False
        assert any(
            r.name == "anima.runtime.degraded" and "contract mismatch" in r.getMessage()
            for r in caplog.records
        )
        with rt_factory() as rt_db:
            row = rt_db.scalar(select(EmbeddingConfig))
        assert row.reembed_required is True
        # The old pair stays recorded until the re-embed completes.
        assert row.embedding_model == "nomic-embed-text"

        # The flag survives a "restart" (fresh process cache).
        embedding_contract.reset_contract_cache()
        assert embedding_contract.is_reembed_required(runtime_db_factory=rt_factory) is True

    def test_complete_reembed_adopts_new_pair(self, rt_factory) -> None:
        embedding_contract.check_embedding_contract(
            model="nomic-embed-text", dim=768, runtime_db_factory=rt_factory
        )
        embedding_contract.check_embedding_contract(
            model="mxbai-embed-large", dim=1024, runtime_db_factory=rt_factory
        )
        embedding_contract.complete_reembed(
            model="mxbai-embed-large", dim=1024, runtime_db_factory=rt_factory
        )
        with rt_factory() as rt_db:
            row = rt_db.scalar(select(EmbeddingConfig))
        assert row.embedding_model == "mxbai-embed-large"
        assert row.embedding_dim == 1024
        assert row.reembed_required is False
        assert embedding_contract.is_reembed_required(runtime_db_factory=rt_factory) is False

    def test_reembed_gate_is_per_user(self, rt_factory) -> None:
        """A model switch opens a re-embed cycle for everyone; one user
        finishing must not re-enable semantic search for others whose vectors
        are still stale (re-embed is per-user)."""
        embedding_contract.reset_contract_cache()
        embedding_contract.check_embedding_contract(
            model="nomic-embed-text", dim=768, runtime_db_factory=rt_factory
        )
        embedding_contract.check_embedding_contract(
            model="mxbai-embed-large", dim=1024, runtime_db_factory=rt_factory
        )
        embedding_contract.reset_contract_cache()

        # Both users gated until each re-embeds; the global (user_id=None)
        # check still reports the cycle open.
        assert embedding_contract.is_reembed_required(runtime_db_factory=rt_factory) is True
        assert embedding_contract.is_reembed_required(1, runtime_db_factory=rt_factory) is True
        assert embedding_contract.is_reembed_required(2, runtime_db_factory=rt_factory) is True

        # User 1 completes → only user 1 is ungated.
        embedding_contract.mark_user_reembed_complete(1, runtime_db_factory=rt_factory)
        assert embedding_contract.is_reembed_required(1, runtime_db_factory=rt_factory) is False
        assert embedding_contract.is_reembed_required(2, runtime_db_factory=rt_factory) is True

        # Survives a restart (fresh process cache).
        embedding_contract.reset_contract_cache()
        assert embedding_contract.is_reembed_required(1, runtime_db_factory=rt_factory) is False
        assert embedding_contract.is_reembed_required(2, runtime_db_factory=rt_factory) is True


def test_semantic_leg_reports_degraded_instead_of_silent_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import embeddings

    monkeypatch.setattr(
        embedding_contract, "is_reembed_required", lambda *a, **k: True
    )
    ranked = embeddings._semantic_ranked_ids(
        db=None,
        user_id=1,
        query_embedding=_embedding(),
        limit=5,
        similarity_threshold=0.25,
    )
    assert ranked == []


class TestDerivedStoreMaintenance:
    def _add_item_with_embedding(
        self, soul_factory, rt_factory, *, item_content: str
    ) -> int:
        with soul_factory() as db:
            item = MemoryItem(
                user_id=1,
                content=item_content,
                category="fact",
                importance=3,
                source="extraction",
                embedding_json=_embedding(),
            )
            db.add(item)
            db.commit()
            item_id = item.id
        with rt_factory() as rt_db:
            rt_db.add(
                RuntimeEmbedding(
                    user_id=1,
                    source_type="memory_item",
                    source_id=item_id,
                    content_hash="h" * 64,
                    embedding_checksum=RuntimeEmbedding.compute_embedding_checksum(
                        _embedding()
                    ),
                    embedding=_embedding(),
                    content_preview=item_content,
                )
            )
            rt_db.commit()
        return item_id

    def test_reset_clears_soul_and_runtime_stores(
        self, soul_factory, rt_factory
    ) -> None:
        self._add_item_with_embedding(
            soul_factory, rt_factory, item_content="Likes green tea"
        )
        with soul_factory() as db:
            cleared = embedding_contract.reset_derived_embedding_stores(
                db, user_id=1, runtime_db_factory=rt_factory
            )
            db.commit()
        assert cleared == 1
        with soul_factory() as db:
            item = db.scalar(select(MemoryItem))
            assert item.embedding_json is None
        with rt_factory() as rt_db:
            assert rt_db.scalar(select(RuntimeEmbedding)) is None

    def test_orphan_sweep_removes_rows_without_live_items(
        self, soul_factory, rt_factory
    ) -> None:
        live_id = self._add_item_with_embedding(
            soul_factory, rt_factory, item_content="Likes green tea"
        )
        with rt_factory() as rt_db:
            rt_db.add(
                RuntimeEmbedding(
                    user_id=1,
                    source_type="memory_item",
                    source_id=live_id + 999,  # no such memory item
                    content_hash="o" * 64,
                    embedding_checksum=RuntimeEmbedding.compute_embedding_checksum(
                        _embedding()
                    ),
                    embedding=_embedding(),
                    content_preview="orphan",
                )
            )
            rt_db.commit()

        with soul_factory() as db:
            removed = embedding_contract.sweep_orphaned_runtime_embeddings(
                db, user_id=1, runtime_db_factory=rt_factory
            )
        assert removed == 1
        with rt_factory() as rt_db:
            remaining = rt_db.scalars(select(RuntimeEmbedding)).all()
        assert [row.source_id for row in remaining] == [live_id]


def test_upsert_failure_flags_user_for_resync(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from anima_server.services.agent.embeddings import _mark_vector_upsert_failed
    from anima_server.services.agent.vector_store import consume_vector_store_dirty

    with caplog.at_level(logging.WARNING, logger="anima.runtime.degraded"):
        _mark_vector_upsert_failed(7, 42)

    assert any(
        r.name == "anima.runtime.degraded" and "upsert failed" in r.getMessage()
        for r in caplog.records
    )
    assert consume_vector_store_dirty(7) is True
    assert consume_vector_store_dirty(7) is False


def test_clear_embedding_cache_rearms_sync_and_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.services.agent import vector_store
    from anima_server.services.agent.embeddings import clear_embedding_cache

    vector_store._synced_users.add(7)
    embedding_contract._reembed_required = False

    clear_embedding_cache()

    assert 7 not in vector_store._synced_users
    assert embedding_contract._reembed_required is None
