from __future__ import annotations

from types import SimpleNamespace

from anima_server.services.corefs import logical


def test_validation_snapshot_and_read_wrappers_bind_selected_head() -> None:
    calls: list[tuple[object, ...]] = []

    def fake_snapshot(keys: object) -> dict[str, object]:
        calls.append(("snapshot", keys))
        return {"generation": 7, "catalogHash": "abc123"}

    def fake_stat(*args: object) -> bytes:
        calls.append(("stat", *args))
        return b'{"version":"corefs-logical-v1","result":{}}'

    native_session = SimpleNamespace(
        validation_snapshot=fake_snapshot,
        stat_v1=fake_stat,
    )
    keys = object()
    selected = logical.select_validation_snapshot(
        corefs_session=native_session,
        keys=keys,
    )
    assert selected == logical.CoreFsValidationSnapshot(generation=7, catalog_hash="abc123")
    assert logical.stat_v1(
        corefs_session=native_session,
        keys=keys,
        selected=selected,
        path="Diary/today.md",
    ) == b'{"version":"corefs-logical-v1","result":{}}'
    assert calls == [
        ("snapshot", keys),
        ("stat", keys, 7, "abc123", "Diary/today.md"),
    ]


def test_validation_snapshot_and_read_use_one_resolved_native_session() -> None:
    calls: list[tuple[object, ...]] = []

    def validation_snapshot(keys: object) -> dict[str, object]:
        calls.append(("snapshot", keys))
        return {"generation": 7, "catalogHash": "abc123"}

    def stat_v1(
        keys: object,
        generation: int,
        catalog_hash: str,
        path: str,
    ) -> bytes:
        calls.append(("stat", keys, generation, catalog_hash, path))
        return b'{"version":"corefs-logical-v1","result":{}}'

    native_session = SimpleNamespace(
        validation_snapshot=validation_snapshot,
        stat_v1=stat_v1,
    )
    keys = object()

    selected = logical.select_validation_snapshot(
        corefs_session=native_session,
        keys=keys,
    )
    assert logical.stat_v1(
        corefs_session=native_session,
        keys=keys,
        selected=selected,
        path="Diary/today.md",
    ) == b'{"version":"corefs-logical-v1","result":{}}'
    assert calls == [
        ("snapshot", keys),
        ("stat", keys, 7, "abc123", "Diary/today.md"),
    ]


def test_read_chunk_preserves_empty_result() -> None:
    def fake_read_chunk(*_args: object, **_kwargs: object) -> None:
        return None

    assert (
        logical.read_chunk_v1(
            corefs_session=SimpleNamespace(read_chunk_v1=fake_read_chunk),
            keys=object(),
            selected=logical.CoreFsValidationSnapshot(generation=1, catalog_hash="hash"),
            path="empty.md",
        )
        is None
    )


def test_glob_and_grep_wrappers_pass_continuation_cursors() -> None:
    calls: list[tuple[object, ...]] = []

    def fake_glob(*args: object, **kwargs: object) -> bytes:
        calls.append(("glob", *args, kwargs))
        return b'{"version":"corefs-logical-v1","result":{"matches":[]}}'

    def fake_grep(*args: object, **kwargs: object) -> bytes:
        calls.append(("grep", *args, kwargs))
        return b'{"version":"corefs-logical-v1","result":{"matches":[]}}'

    native_session = SimpleNamespace(glob_v1=fake_glob, grep_v1=fake_grep)
    selected = logical.CoreFsValidationSnapshot(generation=3, catalog_hash="hash")
    logical.glob_v1(
        corefs_session=native_session,
        keys="keys",
        selected=selected,
        root="Notes",
        pattern="*.md",
        max_results=10,
        cursor=logical.CoreFsGlobCursor(after="Notes/A.md"),
        response_bytes=1024,
    )
    logical.grep_v1(
        corefs_session=native_session,
        keys="keys",
        selected=selected,
        root="Notes",
        query="needle",
        cursor=logical.CoreFsGrepCursor(
            path="Notes/A.md",
            byte_offset=42,
            walk_after="Notes",
        ),
        response_bytes=1024,
    )

    assert calls == [
        (
            "glob",
            "keys",
            3,
            "hash",
            "Notes",
            "*.md",
            10,
            "Notes/A.md",
            {"response_bytes": 1024},
        ),
        (
            "grep",
            "keys",
            3,
            "hash",
            "Notes",
            "needle",
            False,
            1000,
            100,
            4096,
            "Notes/A.md",
            42,
            "Notes",
            {"response_bytes": 1024},
        ),
    ]


def test_mutation_wrappers_return_migration_frozen_code(monkeypatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_mkdir(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append((args, kwargs))
        return logical.frozen_mutation_result("mkdir")

    monkeypatch.setattr(logical.anima_core, "corefs_mkdir", fake_mkdir, raising=False)

    assert logical.mkdir("Notes", recursive=True) == {
        "ok": False,
        "operation": "mkdir",
        "code": logical.CORE_FS_MIGRATION_WRITE_FROZEN,
    }
    assert calls == [(("Notes",), {"recursive": True})]
