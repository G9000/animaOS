from __future__ import annotations

import base64
import json
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
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
from sqlalchemy import create_engine, inspect, text
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
    assert snapshot.capabilities == frozenset(
        {IndexCapability.NAVIGATION, IndexCapability.EXACT_SEARCH}
    )


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

    def blocking_rebuild(current) -> None:
        calls.append(current)
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
    assert calls == [session]


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
