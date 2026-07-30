from __future__ import annotations

import pytest
from anima_server.services.corefs.indexer import (
    CoreFSProgressiveIndex,
    CoreFSRuntimeLocked,
    IndexCapability,
    ReadinessState,
)
from anima_server.services.sessions import UnlockSessionStore


def test_indexer_publishes_catalog_before_text_and_semantic_readiness() -> None:
    index = CoreFSProgressiveIndex("core-index")

    assert index.snapshot().state is ReadinessState.LOCKED
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    assert index.snapshot().state is ReadinessState.VALIDATING_CORE

    index.begin_catalog()
    index.publish_catalog(
        catalog_generation=7,
        families={"notes": 2, "gallery": 1},
        degraded={"gallery": ("object-bad",)},
    )
    catalog = index.snapshot()

    assert catalog.state is ReadinessState.CATALOG_READY_DEGRADED
    assert catalog.catalog_generation == 7
    assert catalog.capabilities == frozenset(
        {
            IndexCapability.NAVIGATION,
        }
    )
    assert catalog.families["notes"].failed == 0
    assert catalog.families["gallery"].failed == 1
    assert catalog.families["gallery"].degraded is True

    index.begin_text_indexing()
    index.index_text(
        family="notes",
        object_id="note-1",
        revision="rev-1",
        text="alpha private note",
    )
    text_ready = index.snapshot()
    assert text_ready.state is ReadinessState.TEXT_INDEXING
    assert IndexCapability.TEXT_SEARCH in text_ready.capabilities
    assert index.search_text("alpha") == ("note-1",)

    index.begin_semantic_indexing()
    index.index_vector(object_id="note-1", vector=(0.1, 0.2, 0.3))
    index.finish()
    ready = index.snapshot()

    assert ready.state is ReadinessState.READY
    assert IndexCapability.SEMANTIC_SEARCH in ready.capabilities
    assert ready.families["gallery"].degraded is True


def test_indexer_cancel_resume_is_idempotent_and_keeps_canonical_state_untouched() -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    index.begin_catalog()
    index.publish_catalog(
        catalog_generation=11,
        families={"conversations": 3},
    )
    index.begin_text_indexing()
    index.index_text(
        family="conversations",
        object_id="message-1",
        revision="rev-1",
        text="first message",
    )

    checkpoint = index.cancel()
    index.resume(checkpoint)
    index.index_text(
        family="conversations",
        object_id="message-1",
        revision="rev-1",
        text="first message",
    )

    assert index.search_text("first") == ("message-1",)
    assert index.snapshot().processed_objects == 1
    assert checkpoint.catalog_generation == 11
    assert checkpoint.cursor == "message-1:rev-1"


def test_catalog_refresh_retains_instance_binding_for_runtime_sealing() -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")

    index.begin_catalog()

    assert index.local_instance_id == "instance-a"


def test_clear_unlocked_state_revokes_search_keys_vectors_and_queries() -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    index.begin_catalog()
    index.publish_catalog(catalog_generation=1, families={"notes": 1})
    index.begin_text_indexing()
    index.index_text(
        family="notes",
        object_id="note-1",
        revision="rev-1",
        text="private marker",
    )
    index.begin_semantic_indexing()
    index.index_vector(object_id="note-1", vector=(0.5, 0.5))
    query_id = index.begin_query()

    before = index.sensitive_buffer_counts()
    assert before == {
        "documents": 1,
        "vectors": 1,
        "queries": 1,
        "blind_tokens": 0,
        "search_keys": 1,
        "sealing_keys": 1,
    }

    index.clear_unlocked_state()

    assert index.snapshot().state is ReadinessState.LOCKED
    assert index.sensitive_buffer_counts() == {
        "documents": 0,
        "vectors": 0,
        "queries": 0,
        "blind_tokens": 0,
        "search_keys": 0,
        "sealing_keys": 0,
    }
    with pytest.raises(CoreFSRuntimeLocked):
        index.search_text("private")
    with pytest.raises(CoreFSRuntimeLocked):
        index.finish_query(query_id)


def test_semantic_refresh_does_not_interrupt_active_text_indexing() -> None:
    index = CoreFSProgressiveIndex("core-a")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    index.begin_catalog()
    index.publish_catalog(catalog_generation=1, families={"note": 2})
    index.begin_text_indexing()

    index.request_semantic_refresh(embedding_fingerprint="provider-b:model-b")

    assert index.snapshot().state is ReadinessState.TEXT_INDEXING
    index.index_text(
        family="note",
        object_id="note-1",
        revision="1",
        text="first note",
    )
    with pytest.raises(ValueError, match="configuration changed"):
        index.begin_semantic_indexing(
            embedding_fingerprint="provider-a:model-a",
        )

    assert index.snapshot().state is ReadinessState.TEXT_INDEXING
    index.begin_semantic_indexing(
        embedding_fingerprint="provider-b:model-b",
    )
    assert index.snapshot().state is ReadinessState.SEMANTIC_INDEXING


def test_blind_token_generation_switch_is_atomic_and_instance_bound() -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")

    index.begin_blind_generation(generation=3, expected_count=2)
    index.add_blind_token(generation=3, value="Alpha", object_id="note-1")
    with pytest.raises(ValueError, match="incomplete"):
        index.commit_blind_generation(3)

    index.add_blind_token(generation=3, value="Beta", object_id="note-2")
    index.commit_blind_generation(3)
    assert index.lookup_exact(" alpha ") == ("note-1",)

    index.begin_blind_generation(generation=4, expected_count=1)
    index.add_blind_token(generation=4, value="Alpha", object_id="note-3")
    assert index.lookup_exact("alpha") == ("note-1",)
    index.commit_blind_generation(4)
    assert index.lookup_exact("alpha") == ("note-3",)

    another_instance = CoreFSProgressiveIndex("core-index")
    another_instance.unlock(
        sqlcipher_key=b"s" * 32,
        local_instance_id="instance-b",
    )
    another_instance.load_blind_generation(
        generation=4,
        entries=((index.blind_token("alpha"), "note-3"),),
    )
    assert another_instance.lookup_exact("alpha") == ()


def test_exact_search_capability_requires_current_catalog_blind_generation() -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    index.begin_catalog()
    index.publish_catalog(catalog_generation=4, families={"notes": 1})

    assert IndexCapability.EXACT_SEARCH not in index.snapshot().capabilities

    index.load_blind_generation(
        generation=3,
        entries=((index.blind_token("alpha"), "note-old"),),
    )
    assert IndexCapability.EXACT_SEARCH not in index.snapshot().capabilities

    index.begin_blind_generation(generation=4, expected_count=1)
    index.add_blind_token(
        generation=4,
        value="Alpha",
        object_id="note-current",
    )
    assert IndexCapability.EXACT_SEARCH not in index.snapshot().capabilities

    index.commit_blind_generation(4)
    assert IndexCapability.EXACT_SEARCH in index.snapshot().capabilities


def test_blind_tokens_and_generation_state_are_purged_on_lock() -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    index.begin_blind_generation(generation=1, expected_count=1)
    index.add_blind_token(generation=1, value="secret", object_id="note-1")
    index.commit_blind_generation(1)

    assert index.lookup_exact("secret") == ("note-1",)
    index.clear_unlocked_state()

    with pytest.raises(CoreFSRuntimeLocked):
        index.lookup_exact("secret")
    assert index.sensitive_buffer_counts()["blind_tokens"] == 0


def test_catalog_refresh_preserves_independent_runtime_embeddings() -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    index.upsert_runtime_embedding(
        source_type="memory_item",
        source_id=91,
        vector=(1.0, 0.0),
        content="private Runtime memory",
        category="fact",
        importance=4,
    )

    index.begin_catalog()
    index.publish_catalog(catalog_generation=1, families={"notes": 0})

    hits = index.search_runtime_embeddings((1.0, 0.0), limit=5)
    assert [(hit.source_type, hit.source_id) for hit in hits] == [("memory_item", 91)]


def test_unlock_session_revocation_clears_the_attached_runtime_index() -> None:
    class NativeSession:
        def begin_close(self) -> None:
            pass

        def close(self) -> None:
            pass

    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    index.begin_catalog()
    index.publish_catalog(catalog_generation=1, families={"notes": 1})
    index.begin_text_indexing()
    index.index_text(
        family="notes",
        object_id="note-1",
        revision="rev-1",
        text="private marker",
    )
    store = UnlockSessionStore(
        corefs_session_factory=NativeSession,
        runtime_index_factory=lambda _keys, _sqlcipher_key: index,
    )

    token = store.create(7, {"memories": b"m" * 32}, corefs_keys=object())
    session = store.resolve(token)
    assert session is not None
    assert session.runtime_index is index

    store.revoke(token)

    assert index.snapshot().state is ReadinessState.LOCKED
    assert index.sensitive_buffer_counts()["documents"] == 0


def test_unlock_session_factory_receives_process_sqlcipher_key() -> None:
    class NativeSession:
        def begin_close(self) -> None:
            pass

        def close(self) -> None:
            pass

    seen: list[bytes | None] = []
    store = UnlockSessionStore(
        corefs_session_factory=NativeSession,
        runtime_index_factory=lambda _keys, key: seen.append(key) or None,
    )
    store.set_sqlcipher_key(b"k" * 32)

    token = store.create(8, {"memories": b"m" * 32}, corefs_keys=object())
    try:
        assert seen == [b"k" * 32]
    finally:
        store.revoke(token)
        store.clear_sqlcipher_key()


def test_unlock_session_publish_callback_receives_new_session() -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    published = []
    store = UnlockSessionStore(
        runtime_index_factory=lambda _keys, _key: index,
        on_session_published=published.append,
    )

    token = store.create(8, {"memories": b"m" * 32})
    try:
        session = store.resolve(token)
        assert session is not None
        assert published == [session]
    finally:
        store.revoke(token)


def test_legacy_unlock_session_still_creates_runtime_sealing_index() -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    seen: list[tuple[object | None, bytes | None]] = []
    store = UnlockSessionStore(
        runtime_index_factory=lambda keys, key: seen.append((keys, key)) or index,
    )
    store.set_sqlcipher_key(b"k" * 32)

    token = store.create(
        9,
        {"memories": b"m" * 32},
        corefs_keys=None,
    )
    try:
        session = store.resolve(token)
        assert session is not None
        assert seen == [(None, b"k" * 32)]
        assert session.runtime_index is index
        assert store.get_active_runtime_index(9) is index
    finally:
        store.revoke(token)
        store.clear_sqlcipher_key()


def test_unlock_store_returns_every_live_runtime_index_for_a_user() -> None:
    indexes = [
        CoreFSProgressiveIndex("core-index"),
        CoreFSProgressiveIndex("core-index"),
    ]
    for index in indexes:
        index.unlock(sqlcipher_key=b"k" * 32, local_instance_id="instance-a")
    remaining = iter(indexes)
    store = UnlockSessionStore(
        runtime_index_factory=lambda _keys, _key: next(remaining),
    )
    store.set_sqlcipher_key(b"k" * 32)

    first = store.create(9, {"memories": b"m" * 32})
    second = store.create(9, {"memories": b"m" * 32})
    try:
        assert store.get_active_runtime_indexes(9) == tuple(indexes)
    finally:
        store.revoke(first)
        store.revoke(second)
        store.clear_sqlcipher_key()
