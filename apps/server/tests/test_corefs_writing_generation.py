from __future__ import annotations

from pathlib import Path

import pytest
from anima_server.db.session import _run_alembic_upgrade, get_user_session_factory
from anima_server.services.corefs import logical
from anima_server.services.corefs.soul_store import _database_inventory
from anima_server.services.corefs.writing_authority import read_canonical_writing_catalog
from anima_server.services.sessions import unlock_session_store
from conftest import managed_test_client
from sqlalchemy import create_engine, inspect

_RETIRED_WRITING_TABLES = {
    "users",
    "diary_entries",
    "diary_folders",
    "diary_attachments",
    "corefs_writing_source_state",
}


def test_fresh_soul_head_has_no_app_writing_tables_or_generation_triggers(managed_tmp_path: Path):
    engine = create_engine(f"sqlite:///{(managed_tmp_path / 'current-soul.db').as_posix()}")
    try:
        _run_alembic_upgrade(engine)
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "20261009_0001"
            )
            assert not _RETIRED_WRITING_TABLES.intersection(inspect(connection).get_table_names())
            assert (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_corefs_writing_source_%'"
                ).all()
                == []
            )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()


def test_untracked_writing_source_is_rejected_without_schema_repair_or_data_loss(
    managed_tmp_path: Path,
):
    engine = create_engine(f"sqlite:///{(managed_tmp_path / 'unsupported-writing.db').as_posix()}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
            connection.exec_driver_sql("INSERT INTO users VALUES (7, 'original-private-owner')")
            original_schema = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master ORDER BY name"
            ).all()
        with pytest.raises(ValueError, match="Unsupported untracked Soul schema"):
            _run_alembic_upgrade(engine)
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT sql FROM sqlite_master ORDER BY name").all()
                == original_schema
            )
            assert connection.exec_driver_sql("SELECT id,username FROM users").all() == [
                (7, "original-private-owner")
            ]
    finally:
        engine.dispose()


def test_current_native_writing_advances_catalog_and_fences_stale_writers_without_soul_mutation():
    with managed_test_client("current-native-writing-generation-") as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "writer", "password": "current-password", "name": "Writer"},
        )
        assert registered.status_code == 201, registered.text
        owner_id = registered.json()["id"]
        headers = {"x-anima-unlock": registered.json()["unlockToken"]}
        session = unlock_session_store.resolve(registered.json()["unlockToken"])
        assert session is not None
        original_catalog = read_canonical_writing_catalog(session=session)
        factory = get_user_session_factory(owner_id)
        with factory() as soul_db, soul_db.get_bind().connect() as connection:
            original_soul = _database_inventory(connection)
            assert not _RETIRED_WRITING_TABLES.intersection(inspect(connection).get_table_names())
        created = client.post(
            "/api/diary",
            headers=headers,
            json={
                "userId": owner_id,
                "entryDate": "2026-10-09",
                "title": "Private native writing",
                "body": "<p>Original private body</p>",
            },
        )
        assert created.status_code == 201, created.text
        after_create = read_canonical_writing_catalog(session=session)
        assert after_create.selection.generation == original_catalog.selection.generation + 1
        assert after_create.selection.catalog_hash != original_catalog.selection.catalog_hash
        entry_id = created.json()["id"]
        updated = client.patch(
            f"/api/diary/{entry_id}", headers=headers, json={"body": "<p>Preserved revision</p>"}
        )
        assert updated.status_code == 200, updated.text
        after_update = read_canonical_writing_catalog(session=session)
        assert after_update.selection.generation == after_create.selection.generation + 1
        assert after_update.entries[0].document.html == "<p>Preserved revision</p>"
        with pytest.raises(logical.CoreFsMutationUnavailable, match="snapshot_stale"):
            logical.execute_mutation_v1(
                corefs_session=session.corefs_session,
                keys=session.corefs_keys,
                selected=original_catalog.selection.snapshot,
                principal="user",
                mutation={
                    "operation": "create_folder",
                    "path": "/Journal/Rejected stale writer",
                    "stableId": "0" * 26,
                },
            )
        after_rejection = read_canonical_writing_catalog(session=session)
        assert after_rejection.selection == after_update.selection
        assert not any(folder.name == "Rejected stale writer" for folder in after_rejection.folders)
        with factory() as soul_db, soul_db.get_bind().connect() as connection:
            assert _database_inventory(connection) == original_soul
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def _historical_engine(database: Path, *, encrypted: bool):
    from sqlalchemy import event
    from sqlalchemy.pool import NullPool

    kwargs = {"module": __import__("sqlcipher3")} if encrypted else {}
    engine = create_engine(f"sqlite:///{database.as_posix()}", poolclass=NullPool, **kwargs)

    @event.listens_for(engine, "connect")
    def configure(connection, _record):
        if encrypted:
            connection.execute(f"PRAGMA key = \"x'{bytes(range(32)).hex()}'\"")
        connection.execute("PRAGMA foreign_keys = ON")

    return engine


def _upgrade_direct(engine, revision):
    from alembic import command
    from alembic.config import Config
    from anima_server.db.session import _ALEMBIC_INI

    config = Config(str(_ALEMBIC_INI))
    with engine.begin() as connection:
        connection.connection.driver_connection.execute("BEGIN IMMEDIATE")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


@pytest.mark.parametrize("encrypted", [False, True])
def test_historical_writing_triggers_retire_before_fk_enabled_table_drops(
    managed_tmp_path: Path, encrypted
):
    engine = _historical_engine(managed_tmp_path / "historical-writing.db", encrypted=encrypted)
    try:
        _upgrade_direct(engine, "20260812_0001")
        with engine.connect() as connection:
            assert (
                len(
                    connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_corefs_writing_source_%'"
                    ).all()
                )
                == 12
            )
        _upgrade_direct(engine, "head")
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_corefs_writing_source_%'"
                ).all()
                == []
            )
            assert not _RETIRED_WRITING_TABLES.intersection(inspect(connection).get_table_names())
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()


def test_writing_trigger_retirement_rolls_back_with_failed_schema_transaction(
    managed_tmp_path: Path, monkeypatch
):
    from alembic import op

    engine = _historical_engine(managed_tmp_path / "writing-rollback.db", encrypted=True)
    try:
        _upgrade_direct(engine, "20260812_0001")
        with engine.connect() as connection:
            original_schema = connection.exec_driver_sql(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
            ).all()
            original_counts = {
                name: connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{name}"').scalar_one()
                for name in inspect(connection).get_table_names()
            }
        original_drop = op.drop_table

        def fail_after_app_drop(name, *args, **kwargs):
            original_drop(name, *args, **kwargs)
            if name == "diary_attachments":
                raise RuntimeError("injected after trigger and table retirement")

        monkeypatch.setattr(op, "drop_table", fail_after_app_drop)
        with pytest.raises(RuntimeError, match="injected after trigger"):
            _run_alembic_upgrade(engine)
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name"
                ).all()
                == original_schema
            )
            assert {
                name: connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{name}"').scalar_one()
                for name in inspect(connection).get_table_names()
            } == original_counts
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()
