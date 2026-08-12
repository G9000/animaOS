from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

import sqlcipher3
from alembic import command
from alembic.config import Config
from anima_server.db.session import _run_alembic_upgrade
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

SERVER_ROOT = Path(__file__).resolve().parents[1]
WRITING_GENERATION_REVISION = "20260812_0001"


def _engine(database: Path, *, encrypted: bool = False) -> Engine:
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        future=True,
        poolclass=NullPool,
        module=sqlcipher3 if encrypted else None,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        if encrypted:
            cursor.execute(f"PRAGMA key = \"x'{bytes(range(32)).hex()}'\"")
            cursor.execute("PRAGMA cipher_page_size = 4096")
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 1000")
        cursor.close()

    return engine


def _migrate(engine: Engine, revision: str, *, downgrade: bool = False) -> None:
    config = Config(str(SERVER_ROOT / "alembic_core.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if downgrade:
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)


def _insert_user(connection, user_id: int, username: str) -> None:  # type: ignore[no-untyped-def]
    connection.execute(
        text(
            "INSERT INTO users (id, username, password_hash, display_name) "
            "VALUES (:id, :username, 'hash', :username)"
        ),
        {"id": user_id, "username": username},
    )


def _generation(connection, user_id: int) -> int | None:  # type: ignore[no-untyped-def]
    return connection.scalar(
        text("SELECT generation FROM corefs_writing_source_state WHERE user_id = :user_id"),
        {"user_id": user_id},
    )


def _insert_entry(connection, *, user_id: int, body: str = "body") -> int:  # type: ignore[no-untyped-def]
    result = connection.execute(
        text(
            "INSERT INTO diary_entries (user_id, entry_date, body, source) "
            "VALUES (:user_id, '2026-08-12', :body, 'user')"
        ),
        {"user_id": user_id, "body": body},
    )
    return int(result.lastrowid)


def test_writing_generation_migration_supports_prior_head_and_roundtrip(
    managed_tmp_path: Path,
) -> None:
    engine = _engine(managed_tmp_path / "writing-generation-migration.db")
    _migrate(engine, "20260721_0001")

    with engine.begin() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'corefs_writing_source_state'"
                )
            )
            == 0
        )

    _migrate(engine, "head")
    with engine.begin() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'corefs_writing_source_state'"
                )
            )
            == 1
        )
        trigger_count = connection.scalar(
            text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'trigger' AND name LIKE 'trg_corefs_writing_source_%'"
            )
        )
        assert trigger_count == 12

    _migrate(engine, "20260802_0001", downgrade=True)
    with engine.begin() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type IN ('table', 'trigger') "
                    "AND (name = 'corefs_writing_source_state' "
                    "OR name LIKE 'trg_corefs_writing_source_%')"
                )
            )
            == 0
        )

    _migrate(engine, "head")
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            WRITING_GENERATION_REVISION
        )
    engine.dispose()


def test_writing_generation_migration_fresh_upgrade(managed_tmp_path: Path) -> None:
    engine = _engine(managed_tmp_path / "writing-generation-fresh.db")
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            WRITING_GENERATION_REVISION
        )
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'corefs_writing_source_state'"
                )
            )
            == 1
        )
    engine.dispose()


def test_legacy_unstamped_database_receives_authoritative_generation_triggers(
    managed_tmp_path: Path,
) -> None:
    engine = _engine(managed_tmp_path / "writing-generation-legacy-create-all.db")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE, "
                "password_hash VARCHAR(255) NOT NULL, display_name VARCHAR(120) NOT NULL)"
            )
        )

    _run_alembic_upgrade(engine)
    with engine.begin() as connection:
        _insert_user(connection, 1, "legacy-writer")
        _insert_entry(connection, user_id=1)
        assert _generation(connection, 1) == 1
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            WRITING_GENERATION_REVISION
        )
    engine.dispose()


def test_all_legacy_writing_mutations_advance_transactionally_and_per_user(
    managed_tmp_path: Path,
) -> None:
    engine = _engine(managed_tmp_path / "writing-generation-triggers.db")
    _migrate(engine, "head")

    with engine.begin() as connection:
        _insert_user(connection, 1, "writer-one")
        _insert_user(connection, 2, "writer-two")

        folder_id = int(
            connection.execute(
                text("INSERT INTO diary_folders (user_id, name) VALUES (1, 'folder')")
            ).lastrowid
        )
        assert _generation(connection, 1) == 1
        assert _generation(connection, 2) is None

        connection.execute(
            text("UPDATE diary_folders SET name = 'renamed' WHERE id = :id"),
            {"id": folder_id},
        )
        assert _generation(connection, 1) == 2

        entry_id = _insert_entry(connection, user_id=1)
        assert _generation(connection, 1) == 3
        connection.execute(
            text("UPDATE diary_entries SET body = 'changed' WHERE id = :id"),
            {"id": entry_id},
        )
        assert _generation(connection, 1) == 4

        attachment_id = int(
            connection.execute(
                text(
                    "INSERT INTO diary_attachments "
                    "(entry_id, user_id, kind, mime_type, size_bytes, storage_path, sha256) "
                    "VALUES (:entry_id, 1, 'file', 'application/octet-stream', 3, 'one.bin', :sha)"
                ),
                {"entry_id": entry_id, "sha": "a" * 64},
            ).lastrowid
        )
        assert _generation(connection, 1) == 5
        connection.execute(
            text("UPDATE diary_attachments SET caption = 'changed' WHERE id = :id"),
            {"id": attachment_id},
        )
        assert _generation(connection, 1) == 6
        connection.execute(
            text("DELETE FROM diary_attachments WHERE id = :id"),
            {"id": attachment_id},
        )
        assert _generation(connection, 1) == 7

        second_entry_id = _insert_entry(connection, user_id=2, body="other")
        assert _generation(connection, 2) == 1
        assert _generation(connection, 1) == 7
        connection.execute(
            text("DELETE FROM diary_entries WHERE id = :id"),
            {"id": second_entry_id},
        )
        assert _generation(connection, 2) == 2

        before_cascade = _generation(connection, 1)
        cascading_attachment = connection.execute(
            text(
                "INSERT INTO diary_attachments "
                "(entry_id, user_id, kind, mime_type, size_bytes, storage_path, sha256) "
                "VALUES (:entry_id, 1, 'file', 'application/octet-stream', 4, 'cascade.bin', :sha)"
            ),
            {"entry_id": entry_id, "sha": "b" * 64},
        )
        assert cascading_attachment.lastrowid is not None
        before_entry_delete = _generation(connection, 1)
        connection.execute(
            text("DELETE FROM diary_entries WHERE id = :id"),
            {"id": entry_id},
        )
        assert _generation(connection, 1) is not None
        assert _generation(connection, 1) >= int(before_entry_delete) + 2
        assert int(before_entry_delete) > int(before_cascade)

        before_folder_delete = _generation(connection, 1)
        connection.execute(
            text("DELETE FROM diary_folders WHERE id = :id"),
            {"id": folder_id},
        )
        assert _generation(connection, 1) == int(before_folder_delete) + 1

    with engine.connect() as connection:
        committed_generation = _generation(connection, 1)

    transaction = engine.connect()
    transaction.exec_driver_sql("BEGIN")
    transaction.execute(text("INSERT INTO diary_folders (user_id, name) VALUES (1, 'rolled back')"))
    assert _generation(transaction, 1) == int(committed_generation) + 1
    transaction.rollback()
    transaction.close()

    with engine.connect() as connection:
        assert _generation(connection, 1) == committed_generation
    engine.dispose()


def test_begin_immediate_blocks_a_second_legacy_writer_until_release(
    managed_tmp_path: Path,
) -> None:
    engine = _engine(managed_tmp_path / "writing-generation-fence.db", encrypted=True)
    _migrate(engine, "head")
    with engine.begin() as connection:
        _insert_user(connection, 1, "fenced-writer")

    fence = engine.connect()
    fence.exec_driver_sql("BEGIN IMMEDIATE")
    attempted = Event()
    finished = Event()
    errors: list[BaseException] = []

    def write_on_second_connection() -> None:
        try:
            with engine.begin() as connection:
                attempted.set()
                connection.execute(
                    text("INSERT INTO diary_folders (user_id, name) VALUES (1, 'blocked')")
                )
        except BaseException as exc:  # pragma: no cover - assertion reports the concrete failure
            errors.append(exc)
        finally:
            finished.set()

    writer = Thread(target=write_on_second_connection, daemon=True)
    writer.start()
    assert attempted.wait(timeout=1)
    assert not finished.wait(timeout=0.05)
    assert _generation(fence, 1) is None

    fence.rollback()
    fence.close()
    assert finished.wait(timeout=1)
    writer.join(timeout=1)
    assert not errors

    with engine.connect() as connection:
        assert _generation(connection, 1) == 1
    engine.dispose()
