from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from anima_server.config import settings
from anima_server.db import session as db_session
from anima_server.db.session import get_user_database_path
from anima_server.db.user_store import list_user_ids
from anima_server.services.core import (
    ensure_core_manifest,
    get_manifest_path,
    set_owner_user_id,
    update_core_manifest,
)
from anima_server.services.corefs import soul_relocation
from anima_server.services.corefs.cutover import CutoverState, begin_migration
from anima_server.services.corefs.soul_relocation import (
    SOUL_DATABASE_RELATIVE_PATH,
    SoulRelocationError,
    active_soul_database_path,
    relocate_owner_soul_database,
    retire_legacy_soul_database_after_cutover,
    rollback_owner_soul_database,
)


def _create_legacy_soul(path: Path, *, value: str = "retained memory") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO alembic_version VALUES ('202608130001')")
        connection.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE)"
        )
        connection.execute("INSERT INTO users VALUES (7, 'owner')")
        connection.execute(
            "CREATE TABLE memory_items ("
            "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, content TEXT NOT NULL, "
            "FOREIGN KEY(user_id) REFERENCES users(id))"
        )
        connection.execute("INSERT INTO memory_items VALUES (1, 7, ?)", (value,))
        connection.commit()
    finally:
        connection.close()


@pytest.fixture()
def isolated_soul(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_session.dispose_cached_engines()
    monkeypatch.setattr(settings, "data_dir", tmp_path / ".anima")
    monkeypatch.setattr(settings, "core_require_encryption", False)
    monkeypatch.setattr(settings, "core_passphrase", "")
    ensure_core_manifest()
    set_owner_user_id(7)
    legacy = settings.data_dir / "users" / "7" / "anima.db"
    _create_legacy_soul(legacy)
    try:
        yield legacy
    finally:
        db_session.dispose_cached_engines()


def _read_memory(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT content FROM memory_items WHERE id = 1").fetchone()
        assert row is not None
        return str(row[0])
    finally:
        connection.close()


def test_relocation_requires_the_write_barrier(isolated_soul: Path) -> None:
    with pytest.raises(SoulRelocationError, match="write barrier"):
        relocate_owner_soul_database(7)
    assert get_user_database_path(7).resolve() == isolated_soul.resolve()


def test_copy_verify_flip_routes_owner_to_single_soul_and_preserves_legacy(
    isolated_soul: Path,
) -> None:
    begin_migration()
    result = relocate_owner_soul_database(7)

    expected_target = (settings.data_dir / SOUL_DATABASE_RELATIVE_PATH).resolve()
    assert result.active_path == expected_target
    assert result.legacy_path == isolated_soul.resolve()
    assert result.inventory_hash
    assert result.table_hashes
    assert _read_memory(expected_target) == "retained memory"
    assert _read_memory(isolated_soul) == "retained memory"
    for database in (expected_target, isolated_soul):
        wal = database.with_name(f"{database.name}-wal")
        assert not wal.exists() or wal.stat().st_size == 0
    assert get_user_database_path(7).resolve() == expected_target
    assert list_user_ids() == [7]

    manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
    assert manifest["soul_database"] == {
        "version": 1,
        "state": "active",
        "legacyUserId": 7,
        "activePath": "soul/soul.db",
        "legacyPath": "users/7/anima.db",
        "inventoryHash": result.inventory_hash,
        "tableHashes": [
            {"table": table, "sha256": digest} for table, digest in result.table_hashes
        ],
    }

    replay = relocate_owner_soul_database(7)
    assert replay == result


def test_crash_after_target_rename_resumes_without_overwriting_verified_target(
    isolated_soul: Path,
) -> None:
    begin_migration()

    def crash(name: str) -> None:
        if name == "soul:after_target_rename":
            raise OSError("simulated relocation crash")

    with pytest.raises(OSError, match="relocation crash"):
        relocate_owner_soul_database(7, boundary_hook=crash)
    target = settings.data_dir / SOUL_DATABASE_RELATIVE_PATH
    assert target.is_file()
    assert "soul_database" not in json.loads(get_manifest_path().read_text(encoding="utf-8"))

    result = relocate_owner_soul_database(7)
    assert result.active_path == target.resolve()
    assert get_user_database_path(7).resolve() == target.resolve()


def test_target_or_source_change_never_flips_manifest(isolated_soul: Path) -> None:
    begin_migration()

    def corrupt_target(name: str) -> None:
        if name == "soul:after_target_rename":
            target = settings.data_dir / SOUL_DATABASE_RELATIVE_PATH
            connection = sqlite3.connect(target)
            try:
                connection.execute("UPDATE memory_items SET content = 'tampered'")
                connection.commit()
            finally:
                connection.close()

    with pytest.raises(SoulRelocationError, match="retained-table verification"):
        relocate_owner_soul_database(7, boundary_hook=corrupt_target)
    assert "soul_database" not in json.loads(get_manifest_path().read_text(encoding="utf-8"))

    target = settings.data_dir / SOUL_DATABASE_RELATIVE_PATH
    target.unlink()

    def change_source(name: str) -> None:
        if name == "soul:after_target_verify":
            connection = sqlite3.connect(isolated_soul)
            try:
                connection.execute("UPDATE memory_items SET content = 'changed during copy'")
                connection.commit()
            finally:
                connection.close()

    with pytest.raises(SoulRelocationError, match="changed during relocation"):
        relocate_owner_soul_database(7, boundary_hook=change_source)
    assert "soul_database" not in json.loads(get_manifest_path().read_text(encoding="utf-8"))


def test_invalid_or_cross_owner_manifest_routing_fails_closed(isolated_soul: Path) -> None:
    def corrupt(manifest: dict[str, object]) -> None:
        manifest["soul_database"] = {
            "version": 1,
            "state": "active",
            "legacyUserId": 8,
            "activePath": "../escape.db",
        }

    update_core_manifest(corrupt)
    with pytest.raises(SoulRelocationError, match="manifest record is invalid"):
        get_user_database_path(7)


def test_reversible_rejection_restores_legacy_soul_routing(isolated_soul: Path) -> None:
    begin_migration()
    relocated = relocate_owner_soul_database(7)

    assert rollback_owner_soul_database(7) is True
    assert active_soul_database_path(7) is None
    assert get_user_database_path(7).resolve() == isolated_soul.resolve()
    assert relocated.active_path.is_file()
    assert rollback_owner_soul_database(7) is False


@pytest.mark.parametrize(
    "crash_boundary",
    ["soul-retirement:after_sidecars", "soul-retirement:after_database"],
)
def test_forward_only_retirement_resumes_without_recreating_legacy_soul(
    isolated_soul: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_boundary: str,
) -> None:
    begin_migration()
    relocated = relocate_owner_soul_database(7)
    monkeypatch.setattr(
        soul_relocation,
        "read_cutover_record",
        lambda: SimpleNamespace(state=CutoverState.CORE_FS_AUTHORITATIVE_FORWARD_ONLY),
    )

    def crash(boundary: str) -> None:
        if boundary == crash_boundary:
            raise OSError("simulated retirement crash")

    with pytest.raises(OSError, match="retirement crash"):
        retire_legacy_soul_database_after_cutover(boundary_hook=crash)

    assert retire_legacy_soul_database_after_cutover() is True
    assert not isolated_soul.exists()
    assert relocated.active_path.is_file()
    assert get_user_database_path(7).resolve() == relocated.active_path
