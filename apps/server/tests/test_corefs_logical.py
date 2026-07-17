from __future__ import annotations

from anima_server.services.corefs import logical


def test_validation_snapshot_and_read_wrappers_bind_selected_head(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    def fake_snapshot(core_root: str, core_id: str, keys: object) -> dict[str, object]:
        calls.append(("snapshot", core_root, core_id, keys))
        return {"generation": 7, "catalogHash": "abc123"}

    def fake_stat(*args: object) -> bytes:
        calls.append(("stat", *args))
        return b'{"version":"corefs-logical-v1","result":{}}'

    monkeypatch.setattr(logical.anima_core, "corefs_validation_snapshot", fake_snapshot, raising=False)
    monkeypatch.setattr(logical.anima_core, "corefs_stat_v1", fake_stat, raising=False)

    keys = object()
    selected = logical.select_validation_snapshot(
        core_root="C:/core",
        core_id="core-1",
        keys=keys,
    )
    assert selected == logical.CoreFsValidationSnapshot(generation=7, catalog_hash="abc123")
    assert logical.stat_v1(
        core_root="C:/core",
        core_id="core-1",
        keys=keys,
        selected=selected,
        path="Diary/today.md",
    ) == b'{"version":"corefs-logical-v1","result":{}}'
    assert calls == [
        ("snapshot", "C:/core", "core-1", keys),
        ("stat", "C:/core", "core-1", keys, 7, "abc123", "Diary/today.md"),
    ]


def test_read_chunk_preserves_empty_result(monkeypatch) -> None:
    def fake_read_chunk(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(logical.anima_core, "corefs_read_chunk_v1", fake_read_chunk, raising=False)

    assert (
        logical.read_chunk_v1(
            core_root="C:/core",
            core_id="core-1",
            keys=object(),
            selected=logical.CoreFsValidationSnapshot(generation=1, catalog_hash="hash"),
            path="empty.md",
        )
        is None
    )


def test_glob_and_grep_wrappers_pass_continuation_cursors(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []

    def fake_glob(*args: object, **kwargs: object) -> bytes:
        calls.append(("glob", *args, kwargs))
        return b'{"version":"corefs-logical-v1","result":{"matches":[]}}'

    def fake_grep(*args: object, **kwargs: object) -> bytes:
        calls.append(("grep", *args, kwargs))
        return b'{"version":"corefs-logical-v1","result":{"matches":[]}}'

    monkeypatch.setattr(logical.anima_core, "corefs_glob_v1", fake_glob, raising=False)
    monkeypatch.setattr(logical.anima_core, "corefs_grep_v1", fake_grep, raising=False)

    selected = logical.CoreFsValidationSnapshot(generation=3, catalog_hash="hash")
    logical.glob_v1(
        core_root="C:/core",
        core_id="core-1",
        keys="keys",
        selected=selected,
        root="Notes",
        pattern="*.md",
        max_results=10,
        cursor=logical.CoreFsGlobCursor(after="Notes/A.md"),
        response_bytes=1024,
    )
    logical.grep_v1(
        core_root="C:/core",
        core_id="core-1",
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
            "C:/core",
            "core-1",
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
            "C:/core",
            "core-1",
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
    def fake_mkdir() -> dict[str, object]:
        return logical.frozen_mutation_result("mkdir")

    monkeypatch.setattr(logical.anima_core, "corefs_mkdir", fake_mkdir, raising=False)

    assert logical.mkdir() == {
        "ok": False,
        "operation": "mkdir",
        "code": logical.CORE_FS_MIGRATION_WRITE_FROZEN,
    }
