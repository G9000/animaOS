from __future__ import annotations

import base64
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from anima_server.models.corefs_runtime import (
    CoreFSBlindToken,
    CoreFSIndexCheckpoint,
    CoreFSIndexEntry,
)
from anima_server.services.corefs import migration as corefs_migration
from anima_server.services.corefs.indexer import (
    CoreFSProgressiveIndex,
    IndexCapability,
    ReadinessState,
)
from anima_server.services.corefs.migration import (
    rebuild_unlocked_search,
    reconcile_authenticated_catalog,
    schedule_unlocked_rebuild,
)
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine

SERVER_ROOT = Path(__file__).resolve().parents[1]
PRE_KEYSLOT_REVISION = "20260704_0001"


def _migrate(engine: Engine, revision: str, *, downgrade: bool = False) -> None:
    config = Config(str(SERVER_ROOT / "alembic_core.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if downgrade:
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)


def test_soul_keyslot_migration_roundtrips_without_touching_legacy_keys(
    managed_tmp_path: Path,
) -> None:
    database = managed_tmp_path / "corefs-keyslots-migration.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}", future=True)

    _migrate(engine, "head")
    head_inspector = inspect(engine)
    assert head_inspector.has_table("soul_keyslots")
    unique_constraints = {
        constraint["name"]: constraint["column_names"]
        for constraint in head_inspector.get_unique_constraints("soul_keyslots")
    }
    assert unique_constraints["uq_soul_keyslots_identity"] == [
        "owner_id",
        "domain",
        "wrapping_path",
        "key_version",
        "credential_generation",
    ]
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (username, password_hash, display_name) "
                "VALUES ('legacy', 'hash', 'Legacy')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO user_keys "
                "(user_id, domain, kdf_salt, kdf_time_cost, kdf_memory_cost_kib, "
                "kdf_parallelism, kdf_key_length, wrap_iv, wrap_tag, wrapped_dek) "
                "VALUES (1, 'memories', 'salt', 3, 65536, 4, 32, 'iv', 'tag', 'dek')"
            )
        )

    _migrate(engine, PRE_KEYSLOT_REVISION, downgrade=True)
    inspector = inspect(engine)
    assert not inspector.has_table("soul_keyslots")
    assert inspector.has_table("user_keys")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT wrapped_dek FROM user_keys WHERE id = 1")) == "dek"

    _migrate(engine, "head")
    assert inspect(engine).has_table("soul_keyslots")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT wrapped_dek FROM user_keys WHERE id = 1")) == "dek"

    engine.dispose()


def test_reconciliation_authenticates_catalog_before_publishing_navigation() -> None:
    class NativeSession:
        def validation_snapshot(self, keys):
            assert keys is corefs_keys
            return {"generation": 7, "catalogHash": "catalog-hash"}

    corefs_keys = object()
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    session = SimpleNamespace(
        runtime_index=index,
        corefs_session=NativeSession(),
        corefs_keys=corefs_keys,
    )

    selected = reconcile_authenticated_catalog(session)
    snapshot = index.snapshot()

    assert selected.generation == 7
    assert snapshot.state is ReadinessState.CATALOG_READY
    assert snapshot.catalog_generation == 7
    assert snapshot.capabilities == frozenset({IndexCapability.NAVIGATION})


def test_progressive_rebuild_reads_authenticated_catalog_only_into_memory() -> None:
    corefs_keys = object()
    text_by_path = {
        "Notes/one.md": "seeded message marker",
        "Documents/scan.txt": "seeded OCR and source span markers",
    }

    class NativeSession:
        def validation_snapshot(self, keys):
            assert keys is corefs_keys
            return {"generation": 9, "catalogHash": "catalog-hash"}

        def walk_v1(
            self,
            keys,
            generation,
            catalog_hash,
            root,
            cursor_after,
            page_size,
            include_directories,
            **_kwargs,
        ):
            assert (keys, generation, catalog_hash, root) == (
                corefs_keys,
                9,
                "catalog-hash",
                "",
            )
            assert cursor_after is None
            assert page_size == 100
            assert include_directories is False
            return json.dumps(
                {
                    "version": "corefs-logical-v1",
                    "result": {
                        "generation": 9,
                        "entries": [
                            {
                                "path": "Notes/one.md",
                                "stableId": "note-1",
                                "revision": 3,
                                "contentHash": "a" * 64,
                                "kind": "file",
                                "objectKind": "note",
                                "depth": 2,
                            },
                            {
                                "path": "Documents/scan.txt",
                                "stableId": "document-1",
                                "revision": 4,
                                "contentHash": "b" * 64,
                                "kind": "file",
                                "objectKind": "document",
                                "depth": 2,
                            },
                        ],
                        "errors": [],
                        "nextCursor": None,
                        "truncated": False,
                        "limitReached": False,
                    },
                }
            ).encode()

        def read_chunk_v1(
            self,
            keys,
            generation,
            catalog_hash,
            path,
            offset,
            max_bytes,
            **_kwargs,
        ):
            assert (keys, generation, catalog_hash) == (
                corefs_keys,
                9,
                "catalog-hash",
            )
            raw = text_by_path[path].encode()
            if offset >= len(raw):
                return None
            chunk = raw[offset : offset + max_bytes]
            return json.dumps(
                {
                    "version": "corefs-logical-v1",
                    "result": {
                        "generation": 9,
                        "path": path,
                        "stableId": "opaque",
                        "revision": 1,
                        "contentHash": "c" * 64,
                        "offset": offset,
                        "bytesBase64": base64.b64encode(chunk).decode(),
                    },
                }
            ).encode()

    native = NativeSession()

    def build() -> CoreFSProgressiveIndex:
        index = CoreFSProgressiveIndex("core-index")
        index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
        session = SimpleNamespace(
            runtime_index=index,
            corefs_session=native,
            corefs_keys=corefs_keys,
        )
        rebuild_unlocked_search(session)
        return index

    first = build()
    assert first.snapshot().state is ReadinessState.READY
    assert first.search_text("seeded message") == ("note-1",)
    assert first.search_text("seeded OCR") == ("document-1",)
    assert first.lookup_exact("Notes/one.md") == ("note-1",)

    first.clear_unlocked_state()
    assert first.snapshot().state is ReadinessState.LOCKED

    rebuilt = build()
    assert rebuilt.search_text("seeded message") == ("note-1",)
    assert rebuilt.snapshot().processed_objects == 2


def test_blind_generation_persists_and_restores_exact_candidates() -> None:
    from conftest_runtime import runtime_db_session

    entries = [
        {
            "family": "note",
            "path": "Notes/one.md",
            "stable_id": "note-1",
            "revision": "3",
        },
        {
            "family": "document",
            "path": "Documents/scan.txt",
            "stable_id": "document-1",
            "revision": "4",
        },
    ]
    first = CoreFSProgressiveIndex("core-index")
    first.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    first.begin_catalog()
    first.publish_catalog(catalog_generation=9, families={"note": 1, "document": 1})

    with runtime_db_session() as runtime_db:
        corefs_migration._persist_blind_generation(
            runtime_db,
            index=first,
            generation=9,
            entries=entries,
        )
        assert runtime_db.scalar(select(CoreFSBlindToken.id).limit(1)) is not None

        restored = CoreFSProgressiveIndex("core-index")
        restored.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
        restored.begin_catalog()
        restored.publish_catalog(
            catalog_generation=9,
            families={"note": 1, "document": 1},
        )
        assert (
            corefs_migration._restore_blind_generation(
                runtime_db,
                index=restored,
                generation=9,
            )
            is True
        )

    assert restored.lookup_exact("Notes/one.md") == ("note-1",)
    assert restored.lookup_exact("Documents/scan.txt") == ("document-1",)


def test_semantic_embedding_failure_stays_retryable_until_vectors_succeed() -> None:
    corefs_keys = object()
    embedding_attempts = 0

    class NativeSession:
        def validation_snapshot(self, keys):
            assert keys is corefs_keys
            return {"generation": 10, "catalogHash": "catalog-hash"}

        def walk_v1(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "version": "corefs-logical-v1",
                    "result": {
                        "generation": 10,
                        "entries": [
                            {
                                "path": "Notes/retry.md",
                                "stableId": "note-retry",
                                "revision": 1,
                                "contentHash": "d" * 64,
                                "kind": "file",
                                "objectKind": "note",
                                "depth": 2,
                            }
                        ],
                        "errors": [],
                        "nextCursor": None,
                        "truncated": False,
                        "limitReached": False,
                    },
                }
            ).encode()

        def read_chunk_v1(
            self,
            _keys,
            _generation,
            _catalog_hash,
            _path,
            offset,
            _max_bytes,
            **_kwargs,
        ):
            if offset:
                return None
            return json.dumps(
                {
                    "version": "corefs-logical-v1",
                    "result": {
                        "generation": 10,
                        "path": "Notes/retry.md",
                        "stableId": "note-retry",
                        "revision": 1,
                        "contentHash": "d" * 64,
                        "offset": 0,
                        "bytesBase64": base64.b64encode(b"semantic retry marker").decode(),
                    },
                }
            ).encode()

    def flaky_embedder(_text: str) -> tuple[float, ...]:
        nonlocal embedding_attempts
        embedding_attempts += 1
        if embedding_attempts == 1:
            raise ValueError("temporary embedding outage")
        return (1.0, 0.0)

    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    session = SimpleNamespace(
        runtime_index=index,
        corefs_session=NativeSession(),
        corefs_keys=corefs_keys,
    )

    rebuild_unlocked_search(session, embedder=flaky_embedder)

    assert index.snapshot().state is ReadinessState.SEMANTIC_INDEXING
    assert IndexCapability.SEMANTIC_SEARCH not in index.snapshot().capabilities

    rebuild_unlocked_search(session, embedder=flaky_embedder)

    assert embedding_attempts == 2
    assert index.snapshot().state is ReadinessState.READY
    assert IndexCapability.SEMANTIC_SEARCH in index.snapshot().capabilities
    assert index.snapshot().families["note"].degraded is False


def test_semantic_retry_rebuilds_all_vectors_after_embedding_config_changes() -> None:
    from conftest_runtime import runtime_db_session

    corefs_keys = object()
    text_by_path = {
        "Notes/one.md": b"first semantic marker",
        "Notes/two.md": b"second semantic marker",
    }

    class NativeSession:
        def validation_snapshot(self, keys):
            assert keys is corefs_keys
            return {"generation": 11, "catalogHash": "catalog-hash"}

        def walk_v1(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "version": "corefs-logical-v1",
                    "result": {
                        "generation": 11,
                        "entries": [
                            {
                                "path": path,
                                "stableId": f"note-{index}",
                                "revision": 1,
                                "contentHash": str(index) * 64,
                                "kind": "file",
                                "objectKind": "note",
                                "depth": 2,
                            }
                            for index, path in enumerate(text_by_path, start=1)
                        ],
                        "errors": [],
                        "nextCursor": None,
                        "truncated": False,
                        "limitReached": False,
                    },
                }
            ).encode()

        def read_chunk_v1(
            self,
            _keys,
            _generation,
            _catalog_hash,
            path,
            offset,
            _max_bytes,
            **_kwargs,
        ):
            if offset:
                return None
            return json.dumps(
                {
                    "version": "corefs-logical-v1",
                    "result": {
                        "generation": 11,
                        "path": path,
                        "stableId": "opaque",
                        "revision": 1,
                        "contentHash": "c" * 64,
                        "offset": 0,
                        "bytesBase64": base64.b64encode(text_by_path[path]).decode(),
                    },
                }
            ).encode()

    old_calls: list[str] = []

    class OldEmbedder:
        corefs_embedding_fingerprint = "provider-a:model-a"

        def __call__(self, text: str) -> tuple[float, ...]:
            old_calls.append(text)
            if text.startswith("second"):
                raise ValueError("old provider failed the second object")
            return (1.0, 0.0)

    old_embedder = OldEmbedder()
    new_calls: list[str] = []

    class NewEmbedder:
        corefs_embedding_fingerprint = "provider-b:model-b"

        def __call__(self, text: str) -> tuple[float, ...]:
            new_calls.append(text)
            return (0.0, 1.0)

    new_embedder = NewEmbedder()

    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    session = SimpleNamespace(
        runtime_index=index,
        corefs_session=NativeSession(),
        corefs_keys=corefs_keys,
    )

    with runtime_db_session() as runtime_db:
        rebuild_unlocked_search(
            session,
            embedder=old_embedder,
            runtime_db=runtime_db,
        )
        assert index.snapshot().state is ReadinessState.SEMANTIC_INDEXING
        assert old_calls == ["first semantic marker", "second semantic marker"]

        rebuild_unlocked_search(
            session,
            embedder=new_embedder,
            runtime_db=runtime_db,
        )

    assert new_calls == ["first semantic marker", "second semantic marker"]
    assert index.snapshot().state is ReadinessState.READY
    assert index.search_semantic((0.0, 1.0), limit=2) == ("note-1", "note-2")


def test_unlocked_rebuild_scheduler_allows_only_one_worker_per_index(
    monkeypatch,
) -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    index.begin_catalog()
    index.publish_catalog(catalog_generation=1, families={})
    session = SimpleNamespace(runtime_index=index)
    started = Event()
    release = Event()
    completed = Event()
    calls: list[object] = []

    def blocking_rebuild(current, *, embedder=None, runtime_db=None) -> None:
        calls.append((current, embedder, runtime_db))
        started.set()
        assert release.wait(timeout=2)
        completed.set()

    monkeypatch.setattr(
        corefs_migration,
        "rebuild_unlocked_search",
        blocking_rebuild,
    )

    assert schedule_unlocked_rebuild(session) is True
    assert started.wait(timeout=2)
    assert schedule_unlocked_rebuild(session) is False
    release.set()
    assert completed.wait(timeout=2)
    assert len(calls) == 1
    assert calls[0][0] is session
    assert callable(calls[0][1])


def test_unlocked_rebuild_scheduler_queues_forced_refresh_after_active_worker(
    monkeypatch,
) -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    index.begin_catalog()
    index.publish_catalog(catalog_generation=1, families={})
    session = SimpleNamespace(runtime_index=index)
    first_started = Event()
    release_first = Event()
    second_completed = Event()
    calls: list[object] = []

    def blocking_rebuild(current, *, embedder=None, runtime_db=None) -> None:
        calls.append((current, embedder, runtime_db))
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(timeout=2)
        else:
            second_completed.set()

    monkeypatch.setattr(
        corefs_migration,
        "rebuild_unlocked_search",
        blocking_rebuild,
    )

    assert schedule_unlocked_rebuild(session) is True
    assert first_started.wait(timeout=2)
    assert (
        schedule_unlocked_rebuild(
            session,
            rerun_if_active=True,
        )
        is False
    )
    release_first.set()
    assert second_completed.wait(timeout=2)
    assert len(calls) == 2


def test_catalog_reconciliation_queues_behind_active_rebuild(
    monkeypatch,
) -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    session = SimpleNamespace(runtime_index=index)
    first_started = Event()
    release_first = Event()
    second_completed = Event()
    calls: list[object] = []

    def blocking_rebuild(current, *, embedder=None, runtime_db=None) -> None:
        calls.append((current, embedder, runtime_db))
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(timeout=2)
        else:
            second_completed.set()

    monkeypatch.setattr(
        corefs_migration,
        "rebuild_unlocked_search",
        blocking_rebuild,
    )

    assert schedule_unlocked_rebuild(session) is True
    assert first_started.wait(timeout=2)
    assert corefs_migration.reconcile_catalog_if_idle(session) is False
    release_first.set()
    assert second_completed.wait(timeout=2)
    assert len(calls) == 2


def test_empty_catalog_initialization_queues_behind_active_rebuild(
    monkeypatch,
) -> None:
    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    session = SimpleNamespace(runtime_index=index)
    first_started = Event()
    release_first = Event()
    second_completed = Event()
    calls: list[object] = []

    def blocking_rebuild(current, *, embedder=None, runtime_db=None) -> None:
        calls.append((current, embedder, runtime_db))
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(timeout=2)
        else:
            second_completed.set()

    monkeypatch.setattr(
        corefs_migration,
        "rebuild_unlocked_search",
        blocking_rebuild,
    )

    assert schedule_unlocked_rebuild(session) is True
    assert first_started.wait(timeout=2)
    assert corefs_migration.initialize_catalog_if_idle(index, 9) is False
    assert index.snapshot().catalog_generation is None
    release_first.set()
    assert second_completed.wait(timeout=2)
    assert len(calls) == 2


def test_walk_failures_publish_an_observable_degraded_family() -> None:
    corefs_keys = object()

    class NativeSession:
        def validation_snapshot(self, keys):
            assert keys is corefs_keys
            return {"generation": 3, "catalogHash": "catalog-hash"}

        def walk_v1(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "version": "corefs-logical-v1",
                    "result": {
                        "generation": 3,
                        "entries": [],
                        "errors": [
                            {
                                "path": "Notes/corrupt.md",
                                "code": "authentication_failed",
                            }
                        ],
                        "nextCursor": None,
                        "truncated": False,
                        "limitReached": False,
                    },
                }
            ).encode()

        def read_chunk_v1(self, *_args, **_kwargs):
            raise AssertionError("failed walk entries must not be read")

    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    session = SimpleNamespace(
        runtime_index=index,
        corefs_session=NativeSession(),
        corefs_keys=corefs_keys,
    )

    rebuild_unlocked_search(session)

    unknown = index.snapshot().families["unknown"]
    assert unknown.total == 1
    assert unknown.failed == 1
    assert unknown.degraded is True
    assert unknown.unavailable_object_ids == ("Notes/corrupt.md",)


def test_rebuild_retry_uses_durable_progress_without_rereading_completed_text() -> None:
    from conftest_runtime import runtime_db_session

    corefs_keys = object()
    texts = {
        "Notes/one.md": b"first durable marker",
        "Notes/two.md": b"second durable marker",
    }
    reads = {path: 0 for path in texts}
    fail_second = True

    class NativeSession:
        def validation_snapshot(self, keys):
            assert keys is corefs_keys
            return {"generation": 5, "catalogHash": "catalog-hash"}

        def walk_v1(self, *_args, **_kwargs):
            return json.dumps(
                {
                    "version": "corefs-logical-v1",
                    "result": {
                        "generation": 5,
                        "entries": [
                            {
                                "path": path,
                                "stableId": f"note-{index}",
                                "revision": 1,
                                "contentHash": str(index) * 64,
                                "kind": "file",
                                "objectKind": "note",
                                "depth": 2,
                            }
                            for index, path in enumerate(texts, start=1)
                        ],
                        "errors": [],
                        "nextCursor": None,
                        "truncated": False,
                        "limitReached": False,
                    },
                }
            ).encode()

        def read_chunk_v1(
            self,
            _keys,
            _generation,
            _catalog_hash,
            path,
            offset,
            _max_bytes,
            **_kwargs,
        ):
            nonlocal fail_second
            if offset == 0:
                reads[path] += 1
            if path == "Notes/two.md" and offset == 0 and fail_second:
                fail_second = False
                raise RuntimeError("transient native read failure")
            if offset:
                return None
            return json.dumps(
                {
                    "version": "corefs-logical-v1",
                    "result": {
                        "generation": 5,
                        "path": path,
                        "stableId": "opaque",
                        "revision": 1,
                        "contentHash": "c" * 64,
                        "offset": 0,
                        "bytesBase64": base64.b64encode(texts[path]).decode(),
                    },
                }
            ).encode()

    index = CoreFSProgressiveIndex("core-index")
    index.unlock(sqlcipher_key=b"s" * 32, local_instance_id="instance-a")
    session = SimpleNamespace(
        runtime_index=index,
        corefs_session=NativeSession(),
        corefs_keys=corefs_keys,
    )

    with runtime_db_session() as runtime_db:
        with pytest.raises(RuntimeError, match="transient native read failure"):
            rebuild_unlocked_search(session, runtime_db=runtime_db)

        entry = runtime_db.scalar(
            select(CoreFSIndexEntry).where(CoreFSIndexEntry.status == "text_indexed")
        )
        checkpoint = runtime_db.scalar(
            select(CoreFSIndexCheckpoint).where(CoreFSIndexCheckpoint.family == "note")
        )
        assert entry is not None
        assert checkpoint is not None
        assert checkpoint.completed_count == 1

        rebuild_unlocked_search(session, runtime_db=runtime_db)

    assert reads == {
        "Notes/one.md": 1,
        "Notes/two.md": 2,
    }
    assert index.search_text("first durable") == ("note-1",)
    assert index.search_text("second durable") == ("note-2",)
