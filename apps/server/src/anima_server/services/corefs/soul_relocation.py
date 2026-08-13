from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Connection

from anima_server.config import settings
from anima_server.db import session as db_session
from anima_server.services.core import get_manifest_path, update_core_manifest
from anima_server.services.corefs.cutover import CutoverState, read_cutover_record

SOUL_DATABASE_RELATIVE_PATH = "soul/soul.db"
COPY_BUFFER_BYTES = 1024 * 1024
_RELOCATION_STATES = frozenset(
    {
        CutoverState.MIGRATING_WRITE_FROZEN,
        CutoverState.CORE_FS_VALIDATION_READONLY,
        CutoverState.CORE_FS_APPROVED_PENDING_FIRST_WRITE,
    }
)


class SoulRelocationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SoulDatabaseInventory:
    table_names: tuple[str, ...]
    table_hashes: tuple[tuple[str, str], ...]
    combined_hash: str


@dataclass(frozen=True, slots=True)
class SoulRelocationResult:
    legacy_user_id: int
    active_path: Path
    legacy_path: Path
    inventory_hash: str
    table_hashes: tuple[tuple[str, str], ...]


BoundaryHook = Callable[[str], None]


def relocate_owner_soul_database(
    legacy_user_id: int,
    *,
    boundary_hook: BoundaryHook | None = None,
) -> SoulRelocationResult:
    if legacy_user_id <= 0:
        raise SoulRelocationError("legacy Soul owner ID is invalid")
    if read_cutover_record().state not in _RELOCATION_STATES:
        raise SoulRelocationError("Soul relocation requires the migration write barrier")

    legacy_path = (settings.data_dir / "users" / str(legacy_user_id) / "anima.db").resolve()
    active_path = (settings.data_dir / SOUL_DATABASE_RELATIVE_PATH).resolve()
    existing = _read_active_soul_record()
    if existing is not None:
        result = _result_from_record(existing)
        if result.legacy_user_id != legacy_user_id or result.active_path != active_path:
            raise SoulRelocationError("Soul relocation manifest conflicts with this owner")
        _verify_database_at_path(
            active_path,
            expected=result.table_hashes,
            expected_combined_hash=result.inventory_hash,
        )
        return result
    if not legacy_path.is_file():
        raise SoulRelocationError("legacy Soul database is missing")
    if active_path == legacy_path:
        raise SoulRelocationError("Soul relocation source and target must differ")

    source_url = _database_url(legacy_path)
    target_url = _database_url(active_path)
    source_inventory = _checkpoint_and_inventory(source_url)
    _boundary(boundary_hook, "soul:after_source_checkpoint")
    db_session.dispose_all_user_engines()
    db_session.dispose_database(source_url)

    active_path.parent.mkdir(parents=True, exist_ok=True)
    if active_path.exists():
        if not active_path.is_file():
            raise SoulRelocationError("existing Soul relocation target is not a file")
    else:
        _copy_file_durably(legacy_path, active_path)
    _boundary(boundary_hook, "soul:after_target_rename")

    target_inventory = _checkpoint_and_inventory(target_url)
    if target_inventory != source_inventory:
        raise SoulRelocationError("relocated Soul database failed retained-table verification")
    _boundary(boundary_hook, "soul:after_target_verify")

    final_source_inventory = _checkpoint_and_inventory(source_url)
    if final_source_inventory != source_inventory:
        raise SoulRelocationError("legacy Soul database changed during relocation")
    _boundary(boundary_hook, "soul:after_source_reverify")

    final_target_inventory = _checkpoint_and_inventory(target_url)
    if final_target_inventory != source_inventory:
        raise SoulRelocationError("relocated Soul database changed before activation")

    record = {
        "version": 1,
        "state": "active",
        "legacyUserId": legacy_user_id,
        "activePath": SOUL_DATABASE_RELATIVE_PATH,
        "legacyPath": f"users/{legacy_user_id}/anima.db",
        "inventoryHash": source_inventory.combined_hash,
        "tableHashes": [
            {"table": table_name, "sha256": table_hash}
            for table_name, table_hash in source_inventory.table_hashes
        ],
    }

    def activate(manifest: dict[str, object]) -> None:
        owner_user_id = manifest.get("owner_user_id")
        if owner_user_id is None:
            binding = manifest.get("owner_binding")
            if isinstance(binding, dict):
                owner_user_id = binding.get("legacy_user_id")
        if owner_user_id != legacy_user_id:
            raise SoulRelocationError("Soul relocation owner does not match the Core manifest")
        current = manifest.get("soul_database")
        if current is not None and current != record:
            raise SoulRelocationError("Soul relocation manifest changed concurrently")
        manifest["soul_database"] = record

    update_core_manifest(activate)
    _boundary(boundary_hook, "soul:after_manifest_activation")
    if not legacy_path.is_file():
        raise SoulRelocationError("legacy Soul rollback copy disappeared during relocation")
    return _result_from_record(record)


def active_soul_database_path(legacy_user_id: int) -> Path | None:
    record = _read_active_soul_record()
    if record is None:
        return None
    result = _result_from_record(record)
    if result.legacy_user_id != legacy_user_id:
        raise SoulRelocationError("Soul database owner binding is ambiguous")
    return result.active_path


def _read_active_soul_record() -> dict[str, object] | None:
    path = get_manifest_path()
    if not path.is_file():
        return None
    import json

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SoulRelocationError("Core manifest is unreadable during Soul routing") from exc
    raw = manifest.get("soul_database")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SoulRelocationError("Soul database manifest record is invalid")
    return raw


def _result_from_record(record: dict[str, object]) -> SoulRelocationResult:
    expected_keys = {
        "version",
        "state",
        "legacyUserId",
        "activePath",
        "legacyPath",
        "inventoryHash",
        "tableHashes",
    }
    if (
        set(record) != expected_keys
        or record.get("version") != 1
        or record.get("state") != "active"
    ):
        raise SoulRelocationError("Soul database manifest record is invalid")
    legacy_user_id = record.get("legacyUserId")
    active_relative = record.get("activePath")
    legacy_relative = record.get("legacyPath")
    inventory_hash = record.get("inventoryHash")
    raw_hashes = record.get("tableHashes")
    if (
        not isinstance(legacy_user_id, int)
        or isinstance(legacy_user_id, bool)
        or legacy_user_id <= 0
        or active_relative != SOUL_DATABASE_RELATIVE_PATH
        or legacy_relative != f"users/{legacy_user_id}/anima.db"
        or not _is_sha256(inventory_hash)
        or not isinstance(raw_hashes, list)
        or not raw_hashes
    ):
        raise SoulRelocationError("Soul database manifest record is invalid")
    table_hashes: list[tuple[str, str]] = []
    for item in raw_hashes:
        if (
            not isinstance(item, dict)
            or set(item) != {"table", "sha256"}
            or not isinstance(item.get("table"), str)
            or not item["table"]
            or not _is_sha256(item.get("sha256"))
        ):
            raise SoulRelocationError("Soul database table inventory is invalid")
        table_hashes.append((item["table"], item["sha256"]))
    if table_hashes != sorted(table_hashes) or len({item[0] for item in table_hashes}) != len(
        table_hashes
    ):
        raise SoulRelocationError("Soul database table inventory is ambiguous")
    return SoulRelocationResult(
        legacy_user_id=legacy_user_id,
        active_path=(settings.data_dir / SOUL_DATABASE_RELATIVE_PATH).resolve(),
        legacy_path=(settings.data_dir / legacy_relative).resolve(),
        inventory_hash=inventory_hash,
        table_hashes=tuple(table_hashes),
    )


def _checkpoint_and_inventory(database_url: str) -> SoulDatabaseInventory:
    engine = db_session.get_engine(database_url)
    try:
        with engine.connect() as connection:
            checkpoint = connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or int(checkpoint[0]) != 0:
                raise SoulRelocationError("Soul WAL checkpoint is busy or incomplete")
            return _database_inventory(connection)
    finally:
        db_session.dispose_database(database_url)


def _verify_database_at_path(
    path: Path,
    *,
    expected: tuple[tuple[str, str], ...],
    expected_combined_hash: str,
) -> None:
    if not path.is_file():
        raise SoulRelocationError("active Soul database is missing")
    inventory = _checkpoint_and_inventory(_database_url(path))
    if inventory.table_hashes != expected or inventory.combined_hash != expected_combined_hash:
        raise SoulRelocationError("active Soul database inventory does not match its manifest")


def _database_inventory(connection: Connection) -> SoulDatabaseInventory:
    integrity = connection.exec_driver_sql("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        raise SoulRelocationError("Soul database page integrity failed")
    if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone() is not None:
        raise SoulRelocationError("Soul database foreign-key integrity failed")
    cipher_result = connection.exec_driver_sql("PRAGMA cipher_integrity_check")
    cipher_integrity = cipher_result.fetchall() if cipher_result.returns_rows else []
    if cipher_integrity and cipher_integrity != [("ok",)]:
        raise SoulRelocationError("Soul database cipher integrity failed")

    names = tuple(
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )
    if not names or "alembic_version" not in names or "users" not in names:
        raise SoulRelocationError("Soul database schema is incomplete")
    table_hashes = tuple((name, _hash_table(connection, name)) for name in names)
    combined = hashlib.sha256()
    for name, table_hash in table_hashes:
        _hash_field(combined, name.encode("utf-8"))
        _hash_field(combined, bytes.fromhex(table_hash))
    return SoulDatabaseInventory(
        table_names=names,
        table_hashes=table_hashes,
        combined_hash=combined.hexdigest(),
    )


def _hash_table(connection: Connection, table_name: str) -> str:
    quoted_table = _quote_identifier(table_name)
    columns = tuple(
        str(row[1]) for row in connection.exec_driver_sql(f"PRAGMA table_info({quoted_table})")
    )
    if not columns:
        raise SoulRelocationError("Soul database table has no columns")
    quoted_columns = ",".join(_quote_identifier(column) for column in columns)
    digest = hashlib.sha256()
    _hash_field(digest, table_name.encode("utf-8"))
    for column in columns:
        _hash_field(digest, column.encode("utf-8"))
    result = connection.exec_driver_sql(
        f"SELECT {quoted_columns} FROM {quoted_table} ORDER BY {quoted_columns}"
    )
    for row in result:
        for value in row:
            _hash_value(digest, value)
    return digest.hexdigest()


def _hash_value(digest: Any, value: object) -> None:
    if value is None:
        tag, encoded = b"n", b""
    elif isinstance(value, bytes):
        tag, encoded = b"b", value
    elif isinstance(value, str):
        tag, encoded = b"s", value.encode("utf-8")
    elif isinstance(value, int):
        tag, encoded = b"i", str(value).encode("ascii")
    elif isinstance(value, float):
        tag, encoded = b"f", value.hex().encode("ascii")
    else:
        raise SoulRelocationError("Soul database contains an unsupported SQLite value")
    _hash_field(digest, tag)
    _hash_field(digest, encoded)


def _hash_field(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _copy_file_durably(source: Path, target: Path) -> None:
    partial = target.with_name(f"{target.name}.partial")
    partial.unlink(missing_ok=True)
    descriptor = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with source.open("rb") as source_handle, os.fdopen(descriptor, "wb") as target_handle:
            while chunk := source_handle.read(COPY_BUFFER_BYTES):
                target_handle.write(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        if _sha256_file(partial) != _sha256_file(source):
            raise SoulRelocationError("Soul database durable copy hash mismatch")
        _durable_replace(partial, target)
        _fsync_directory(target.parent)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(COPY_BUFFER_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_replace(source: Path, target: Path) -> None:
    if os.name != "nt":
        os.replace(source, target)
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file = kernel32.MoveFileExW
    move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file.restype = wintypes.BOOL
    if not move_file(os.fspath(source), os.fspath(target), 0x00000001 | 0x00000008):
        raise OSError(ctypes.get_last_error(), "MoveFileExW durable Soul activation failed")


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _boundary(hook: BoundaryHook | None, name: str) -> None:
    if hook is not None:
        hook(name)
