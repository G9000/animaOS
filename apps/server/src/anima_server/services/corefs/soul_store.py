"""Canonical Soul database location and verified archive snapshots."""

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

SOUL_DATABASE_RELATIVE_PATH = "soul/soul.db"
SOUL_SNAPSHOT_BACKUP_PAGES = 256


class SoulSnapshotError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SoulDatabaseInventory:
    table_names: tuple[str, ...]
    table_hashes: tuple[tuple[str, str], ...]
    combined_hash: str


BoundaryHook = Callable[[str], None]


def canonical_soul_database_path() -> Path:
    return (settings.data_dir / SOUL_DATABASE_RELATIVE_PATH).resolve()


def create_verified_soul_snapshot(
    source_path: Path,
    snapshot_path: Path,
    *,
    boundary_hook: BoundaryHook | None = None,
) -> SoulDatabaseInventory:
    """Create and independently verify one encrypted point-in-time Soul snapshot."""
    source_candidate = source_path.expanduser()
    if source_candidate.is_symlink():
        raise SoulSnapshotError("Soul snapshot source must be a regular file")
    try:
        source = source_candidate.resolve(strict=True)
    except OSError as exc:
        raise SoulSnapshotError("Soul snapshot source is unavailable") from exc
    if not source.is_file():
        raise SoulSnapshotError("Soul snapshot source must be a regular file")

    target = snapshot_path.expanduser().resolve(strict=False)
    try:
        target_parent = target.parent.resolve(strict=True)
    except OSError as exc:
        raise SoulSnapshotError("Soul snapshot directory is unavailable") from exc
    if target.parent != target_parent or target.exists() or source == target:
        raise SoulSnapshotError("Soul snapshot destination is invalid")

    source_url = _database_url(source)
    target_url = _database_url(target)
    source_engine = db_session.get_engine(source_url)
    target_engine = db_session.get_engine(target_url)
    try:
        with source_engine.connect() as source_connection:
            checkpoint = source_connection.exec_driver_sql(
                "PRAGMA wal_checkpoint(PASSIVE)"
            ).fetchone()
            if (
                checkpoint is None
                or len(checkpoint) != 3
                or int(checkpoint[0]) != 0
                or int(checkpoint[2]) != int(checkpoint[1])
            ):
                raise SoulSnapshotError("Soul WAL checkpoint is busy or incomplete")

            source_driver = _driver_connection(source_connection)
            source_driver.execute("BEGIN")
            try:
                source_driver.execute("SELECT count(*) FROM sqlite_schema").fetchone()
                _boundary(boundary_hook, "soul_snapshot:after_read_pin")
                with target_engine.connect() as target_connection:
                    target_driver = _driver_connection(target_connection)
                    if not hasattr(source_driver, "backup"):
                        raise SoulSnapshotError(
                            "Soul database driver does not support online snapshots"
                        )
                    source_driver.backup(
                        target_driver,
                        pages=SOUL_SNAPSHOT_BACKUP_PAGES,
                        sleep=0.01,
                    )
                    target_driver.commit()
            finally:
                source_driver.rollback()
    except BaseException as exc:
        db_session.dispose_database(target_url)
        _remove_sqlite_snapshot(target)
        if isinstance(exc, SoulSnapshotError):
            raise
        raise SoulSnapshotError("Soul database snapshot failed") from exc
    finally:
        db_session.dispose_database(target_url)

    try:
        inventory = _checkpoint_and_inventory(target_url)
        if not target.is_file() or target.is_symlink():
            raise SoulSnapshotError("Soul database snapshot was not published")
        if os.name != "nt":
            target.chmod(0o600)
        with target.open("rb+") as handle:
            os.fsync(handle.fileno())
        _fsync_directory(target.parent)
        _boundary(boundary_hook, "soul_snapshot:after_verify")
        return inventory
    except BaseException:
        _remove_sqlite_snapshot(target)
        raise


def _checkpoint_and_inventory(database_url: str) -> SoulDatabaseInventory:
    engine = db_session.get_engine(database_url)
    try:
        with engine.connect() as connection:
            checkpoint = connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or int(checkpoint[0]) != 0:
                raise SoulSnapshotError("Soul WAL checkpoint is busy or incomplete")
            return _database_inventory(connection)
    finally:
        db_session.dispose_database(database_url)


def _driver_connection(connection: Connection) -> Any:
    raw = connection.connection
    return getattr(raw, "driver_connection", raw)


def _remove_sqlite_snapshot(path: Path) -> None:
    for candidate in (
        path,
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    ):
        candidate.unlink(missing_ok=True)


def _database_inventory(connection: Connection) -> SoulDatabaseInventory:
    integrity = connection.exec_driver_sql("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        raise SoulSnapshotError("Soul database page integrity failed")
    if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone() is not None:
        raise SoulSnapshotError("Soul database foreign-key integrity failed")
    cipher_result = connection.exec_driver_sql("PRAGMA cipher_integrity_check")
    cipher_integrity = cipher_result.fetchall() if cipher_result.returns_rows else []
    if cipher_integrity and cipher_integrity != [("ok",)]:
        raise SoulSnapshotError("Soul database cipher integrity failed")

    names = tuple(
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )
    if not names or "alembic_version" not in names or "users" not in names:
        raise SoulSnapshotError("Soul database schema is incomplete")
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
        raise SoulSnapshotError("Soul database table has no columns")
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
        raise SoulSnapshotError("Soul database contains an unsupported SQLite value")
    _hash_field(digest, tag)
    _hash_field(digest, encoded)


def _hash_field(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def _boundary(hook: BoundaryHook | None, name: str) -> None:
    if hook is not None:
        hook(name)
