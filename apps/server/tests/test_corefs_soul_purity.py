from __future__ import annotations

import pytest
from anima_server.db.base import Base
from anima_server.models import AgentExperience, AgentSkill, KGEntity, MemoryItem
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture(autouse=True)
def isolated_derived_vectors(monkeypatch):
    from anima_server.config import settings
    from anima_server.services.agent import derived_soul_state as derived
    from anima_server.services.agent import embeddings

    async def unavailable(text):
        return None

    monkeypatch.setattr(settings, "core_require_encryption", False)
    monkeypatch.setattr(embeddings, "generate_embedding", unavailable)
    derived.clear_derived_soul_state()
    yield
    derived.clear_derived_soul_state()


def test_soul_metadata_excludes_disposable_vectors_and_centroids():
    assert "memory_vectors" not in Base.metadata.tables
    assert "experience_cluster_state" not in Base.metadata.tables
    for model in (MemoryItem, KGEntity, AgentExperience, AgentSkill):
        assert "embedding_json" not in model.__table__.columns
        assert "embedding_checksum" not in model.__table__.columns


def test_derived_vectors_are_scoped_copied_and_invalidated(monkeypatch, tmp_path):
    from anima_server.config import settings
    from anima_server.services.agent import derived_soul_state as derived

    monkeypatch.setattr(settings, "data_dir", tmp_path / "one")
    derived.clear_derived_soul_state()
    row = MemoryItem(id=7, user_id=1, content="Remember blue")
    row.embedding_json = [1.0, 0.0]
    vector = row.embedding_json
    vector[0] = 99.0
    assert row.embedding_json == [1.0, 0.0]
    assert MemoryItem(id=7, user_id=2, content="Remember blue").embedding_json is None
    monkeypatch.setattr(settings, "data_dir", tmp_path / "two")
    assert row.embedding_json is None
    monkeypatch.setattr(settings, "data_dir", tmp_path / "one")
    assert row.embedding_json is None  # Switching back must not resurrect prior state.


def test_reload_rebuilds_semantic_vectors_from_retained_text(monkeypatch):
    from anima_server.services.agent import derived_soul_state as derived
    from anima_server.services.agent import embeddings

    calls = []

    async def embed(text):
        calls.append(text)
        return [1.0, 0.0]

    monkeypatch.setattr(embeddings, "generate_embedding", embed)
    derived.clear_derived_soul_state()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        row = MemoryItem(user_id=1, content="Remember blue", category="fact", source="user")
        db.add(row)
        db.commit()
        row_id = row.id
    derived.clear_derived_soul_state()
    with Session(engine) as db:
        row = db.get(MemoryItem, row_id)
        assert row.embedding_json == [1.0, 0.0]
        assert row.embedding_json == [1.0, 0.0]
        assert calls == ["Remember blue"]
        row.content = "Remember green"
        assert row.embedding_json == [1.0, 0.0]
        assert calls[-1] == "Remember green"
    engine.dispose()


def test_centroids_rebuild_without_derived_soul_rows(monkeypatch):
    from anima_server.services.agent import derived_soul_state as derived
    from anima_server.services.agent import embeddings
    from anima_server.services.agent.agent_experience import assign_experience_to_cluster

    async def embed(text):
        return [1.0, 0.0]

    monkeypatch.setattr(embeddings, "generate_embedding", embed)
    derived.clear_derived_soul_state()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first = AgentExperience(
            user_id=1, task_intent="Plan trip", approach="Compare dates", cluster_id="cluster_1_000"
        )
        db.add(first)
        db.commit()
    derived.clear_derived_soul_state()
    with Session(engine) as db:
        second = AgentExperience(
            user_id=1, task_intent="Plan another trip", approach="Compare places"
        )
        db.add(second)
        db.flush()
        assert assign_experience_to_cluster(db, user_id=1, experience=second) == "cluster_1_000"
        assert assign_experience_to_cluster(db, user_id=1, experience=second) == "cluster_1_000"
        state = derived.get_cluster_state(db, user_id=1)
        assert state["clusters"]["cluster_1_000"]["count"] == 2
    engine.dispose()


def test_lock_clears_cache_and_blocks_retained_instance_reads(monkeypatch):
    from anima_server.config import settings
    from anima_server.services import sessions
    from anima_server.services.agent import derived_soul_state as derived

    derived.clear_derived_soul_state()
    row = MemoryItem(id=7, user_id=1, content="private")
    row.embedding_json = [1.0, 0.0]
    monkeypatch.setattr(settings, "core_require_encryption", True)
    monkeypatch.setattr(sessions, "active_unlock_sessions", lambda user_id: ())
    assert row.embedding_json is None
    monkeypatch.setattr(settings, "core_require_encryption", False)
    assert row.embedding_json is None


def test_missing_vector_remains_eligible_for_provider_recovery(monkeypatch):
    from anima_server.services.agent import embeddings
    from anima_server.services.agent.derived_soul_state import _entry

    row = AgentExperience(id=1, user_id=1, task_intent="Plan trip", approach="Compare dates")
    row.embedding_json = None
    assert _entry(row, rebuild=False) is None

    async def embed(text):
        return [1.0, 0.0]

    monkeypatch.setattr(embeddings, "generate_embedding", embed)
    assert row.embedding_json == [1.0, 0.0]


def test_final_session_revocation_clears_owner_only(monkeypatch):
    from anima_server.config import settings
    from anima_server.services import sessions
    from anima_server.services.agent.derived_soul_state import _entry

    store = sessions.UnlockSessionStore(runtime_index_factory=lambda keys, key: None)
    monkeypatch.setattr(settings, "core_require_encryption", True)
    monkeypatch.setattr(sessions, "active_unlock_sessions", store.get_active_sessions)
    first = store.create(1, {"memories": b"a" * 32})
    second = store.create(1, {"memories": b"a" * 32})
    other = store.create(2, {"memories": b"b" * 32})
    row = MemoryItem(id=1, user_id=1, content="one")
    peer = MemoryItem(id=1, user_id=2, content="two")
    row.embedding_json = [1.0, 0.0]
    peer.embedding_json = [0.0, 1.0]
    store.revoke(first)
    assert _entry(row, rebuild=False) is not None
    store.revoke(second)
    assert _entry(row, rebuild=False) is None
    assert peer.embedding_json == [0.0, 1.0]
    store.revoke(other)


@pytest.mark.asyncio
async def test_semantic_retrieval_rebuilds_when_disposable_indexes_are_empty(monkeypatch):
    from anima_server.services.agent import derived_soul_state as derived
    from anima_server.services.agent import embeddings, vector_store

    async def embed(text):
        return [1.0, 0.0] if "trip" in text else [0.0, 1.0]

    monkeypatch.setattr(embeddings, "generate_embedding", embed)
    monkeypatch.setattr(embeddings, "_semantic_ranked_ids_via_rust", lambda **kwargs: None)
    monkeypatch.setattr(vector_store, "search_similar", lambda *args, **kwargs: [])
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                MemoryItem(user_id=1, content="Plan a trip", category="fact", source="user"),
                MemoryItem(user_id=1, content="Debug a server", category="fact", source="user"),
                MemoryItem(user_id=2, content="Plan a trip", category="fact", source="user"),
            ]
        )
        db.commit()
    derived.clear_derived_soul_state()
    with Session(engine) as db:
        results = await embeddings.semantic_search(
            db, user_id=1, query="trip", similarity_threshold=0.8
        )
        assert [(row.content, score) for row, score in results] == [("Plan a trip", 1.0)]
    engine.dispose()


def test_retained_row_cannot_rebuild_private_vectors_under_another_core(monkeypatch, tmp_path):
    from anima_server.config import settings
    from anima_server.services.agent import embeddings

    calls = []

    async def embed(text):
        calls.append(text)
        return [0.0, 1.0]

    monkeypatch.setattr(embeddings, "generate_embedding", embed)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "first")
    row = MemoryItem(id=1, user_id=1, content="First Core private memory")
    row.embedding_json = [1.0, 0.0]
    monkeypatch.setattr(settings, "data_dir", tmp_path / "second")
    assert row.embedding_json is None
    assert calls == []
    assert MemoryItem(id=1, user_id=1, content="Second Core memory").embedding_json == [0.0, 1.0]
    assert calls == ["Second Core memory"]
