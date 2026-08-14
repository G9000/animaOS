from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from anima_server.config import settings
from anima_server.services.core import ensure_core_manifest
from anima_server.services.corefs import legacy_runtime_recovery, logical
from anima_server.services.corefs.cutover import (
    CutoverState,
    approve_validation_cutover,
    begin_migration,
    publish_validation_readonly,
    read_cutover_record,
)


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
    assert (
        logical.stat_v1(
            corefs_session=native_session,
            keys=keys,
            selected=selected,
            path="Diary/today.md",
        )
        == b'{"version":"corefs-logical-v1","result":{}}'
    )
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
    assert (
        logical.stat_v1(
            corefs_session=native_session,
            keys=keys,
            selected=selected,
            path="Diary/today.md",
        )
        == b'{"version":"corefs-logical-v1","result":{}}'
    )
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


def test_approved_first_mutation_uses_manifest_epoch_and_reconciles_head(
    tmp_path,
    monkeypatch,
) -> None:
    validation_hash = "a" * 64
    committed_hash = "b" * 64
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".anima")
    ensure_core_manifest()
    begin_migration()
    publish_validation_readonly(generation=7, catalog_hash=validation_hash)
    pending = approve_validation_cutover()
    calls: list[tuple[dict[str, object], bytes | None]] = []
    restart_signals: list[bool] = []
    monkeypatch.setattr(
        legacy_runtime_recovery,
        "runtime_transition_restart_required",
        lambda: True,
    )
    monkeypatch.setattr(
        legacy_runtime_recovery,
        "mark_runtime_transition_restart_required",
        lambda: restart_signals.append(True),
    )

    class NativeSession:
        marker: dict[str, object] | None = None

        def authoritative_cutover_v1(self, _keys: object) -> dict[str, object] | None:
            return self.marker

        def logical_mutate_v1(
            self,
            _keys: object,
            request_json: str,
            body: bytes | None,
        ) -> dict[str, object]:
            request = json.loads(request_json)
            calls.append((request, body))
            self.marker = {
                "version": 1,
                "legacyRollbackDisabled": True,
                "cutoverEpoch": pending.cutover_epoch,
                "generation": 8,
                "catalogHash": committed_hash,
            }
            return {
                "ok": True,
                "generation": 8,
                "catalogHash": committed_hash,
                "atomic": True,
                "cutoverCommitted": True,
                "recoveryPending": False,
                "invalidationDelivered": False,
                "changes": [{"stableId": "folder-a", "revision": None}],
            }

    invalidations: list[tuple[int, str]] = []
    result = logical.execute_mutation_v1(
        corefs_session=NativeSession(),
        keys="keys",
        selected=logical.CoreFsValidationSnapshot(7, validation_hash),
        principal="user",
        mutation={"operation": "mkdir", "path": "Notes/Projects"},
        invalidate=lambda generation, catalog_hash: invalidations.append(
            (generation, catalog_hash)
        ),
    )

    assert result["generation"] == 8
    assert result["invalidationDelivered"] is True
    assert calls[0][0]["commitMode"] == "first"
    assert calls[0][0]["cutoverEpoch"] == pending.cutover_epoch
    assert calls[0][0]["selectedCatalogHash"] == validation_hash
    assert calls[0][1] is None
    assert invalidations == [(8, committed_hash)]
    cutover = read_cutover_record()
    assert cutover.state is CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY
    assert cutover.authoritative_generation == 8
    assert cutover.authoritative_catalog_hash == committed_hash
    assert restart_signals == [True]


def test_first_mutation_is_blocked_until_runtime_recovery_restart(
    tmp_path,
    monkeypatch,
) -> None:
    validation_hash = "a" * 64
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".anima")
    ensure_core_manifest()
    begin_migration()
    publish_validation_readonly(generation=7, catalog_hash=validation_hash)
    approve_validation_cutover()

    def reject_unprepared_runtime() -> None:
        raise legacy_runtime_recovery.LegacyRuntimeRecoveryError("restart-prepared")

    monkeypatch.setattr(
        legacy_runtime_recovery,
        "require_first_write_runtime_recovery",
        reject_unprepared_runtime,
    )
    native = SimpleNamespace(
        authoritative_cutover_v1=lambda _keys: None,
        logical_mutate_v1=lambda *_args: pytest.fail("native mutation must not run"),
    )

    with pytest.raises(logical.CoreFsMutationUnavailable, match="restart_required"):
        logical.execute_mutation_v1(
            corefs_session=native,
            keys=object(),
            selected=logical.CoreFsValidationSnapshot(7, validation_hash),
            principal="user",
            mutation={"operation": "mkdir", "path": "Notes"},
        )
