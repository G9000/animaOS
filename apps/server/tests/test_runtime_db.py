from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from anima_server.config import settings
from anima_server.db import dispose_cached_engines
from anima_server.db.pg_lifecycle import EmbeddedPG
from anima_server.db.runtime import (
    dispose_runtime_engine,
    get_runtime_engine,
    get_runtime_session_factory,
    init_runtime_engine,
)
from anima_server.db.session import get_db
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.requests import Request

HAS_PGSERVER = importlib.util.find_spec("pgserver") is not None
HAS_PSYCOPG = importlib.util.find_spec("psycopg") is not None or importlib.util.find_spec("psycopg2") is not None
EXPLICIT_RUNTIME_DATABASE_URL = os.getenv("ANIMA_RUNTIME_DATABASE_URL", "").strip()

requires_embedded_pg = pytest.mark.skipif(
    bool(EXPLICIT_RUNTIME_DATABASE_URL) or not HAS_PGSERVER,
    reason=(
        "Embedded PostgreSQL tests require pgserver and are skipped when "
        "ANIMA_RUNTIME_DATABASE_URL points to an external PostgreSQL instance."
    ),
)
requires_runtime_backend = pytest.mark.skipif(
    not HAS_PSYCOPG or (not EXPLICIT_RUNTIME_DATABASE_URL and not HAS_PGSERVER),
    reason=(
        "Runtime PostgreSQL integration tests require psycopg plus either "
        "pgserver or ANIMA_RUNTIME_DATABASE_URL."
    ),
)


@pytest.fixture(autouse=True)
def _reset_runtime_engine_state() -> None:
    dispose_runtime_engine()
    yield
    dispose_runtime_engine()


@pytest.fixture
def runtime_database_url(managed_tmp_path: Path) -> str:
    if EXPLICIT_RUNTIME_DATABASE_URL:
        yield EXPLICIT_RUNTIME_DATABASE_URL
        return

    if not HAS_PGSERVER:
        pytest.skip("pgserver is not installed and no external runtime database URL is configured.")

    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    pg.start()
    try:
        yield pg.database_url
    finally:
        pg.stop()


def _request_with_unlock_header(token: str = "runtime-db-test-token") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-anima-unlock", token.encode("utf-8"))],
        }
    )


def _unique_table_name(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _reload_main_module():
    import importlib

    sys.modules.pop("anima_server.main", None)
    return importlib.import_module("anima_server.main")


def _execute_runtime_sql(sql: str) -> None:
    engine = get_runtime_engine()
    with engine.begin() as connection:
        connection.execute(text(sql))


@requires_embedded_pg
def test_embedded_pg_start_creates_data_directory(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")

    try:
        pg.start()

        assert pg.data_dir.exists()
        assert pg.running is True
    finally:
        pg.stop()


@requires_embedded_pg
def test_embedded_pg_stop_is_idempotent(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    pg.start()

    pg.stop()
    pg.stop()

    assert pg.running is False


@requires_embedded_pg
def test_embedded_pg_database_url_returns_raw_url(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")

    try:
        pg.start()
        assert pg.database_url.startswith("postgresql")
    finally:
        pg.stop()


def test_embedded_pg_database_url_raises_when_not_running(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")

    with pytest.raises(RuntimeError, match="Embedded PG is not running"):
        _ = pg.database_url


def test_embedded_pg_recovers_stale_lockfile(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    pg.data_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pg.data_dir / "postmaster.pid"
    pid_file.write_text("999999\n", encoding="utf-8")

    pg._recover_stale_lockfile()

    assert pid_file.exists() is False


def test_embedded_pg_keeps_valid_lockfile(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    pg.data_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pg.data_dir / "postmaster.pid"
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")

    pg._recover_stale_lockfile()

    assert pid_file.exists() is True


def test_embedded_pg_keeps_lockfile_on_permission_error(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    pg.data_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pg.data_dir / "postmaster.pid"
    pid_file.write_text("42\n", encoding="utf-8")

    def _deny_signal(_pid: int, _sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", _deny_signal)

    pg._recover_stale_lockfile()

    assert pid_file.exists() is True


@requires_runtime_backend
def test_runtime_session_factory_creates_working_sync_sessions(
    runtime_database_url: str,
) -> None:
    init_runtime_engine(runtime_database_url)

    factory = get_runtime_session_factory()
    with factory() as session:
        result = session.execute(text("SELECT 1"))

    assert result.scalar_one() == 1


@requires_runtime_backend
def test_get_runtime_session_commits(runtime_database_url: str) -> None:
    table_name = _unique_table_name("runtime_commit_test")
    init_runtime_engine(runtime_database_url)

    try:
        _execute_runtime_sql(
            f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )

        factory = get_runtime_session_factory()
        with factory() as session:
            session.execute(
                text(f"INSERT INTO {table_name} (id, value) VALUES (1, 'committed')")
            )
            session.commit()

        with factory() as session:
            result = session.execute(text(f"SELECT value FROM {table_name} WHERE id = 1"))

        assert result.scalar_one() == "committed"
    finally:
        _execute_runtime_sql(f"DROP TABLE IF EXISTS {table_name}")


@requires_runtime_backend
def test_get_runtime_session_rolls_back_on_exception(runtime_database_url: str) -> None:
    table_name = _unique_table_name("runtime_rollback_test")
    init_runtime_engine(runtime_database_url)

    try:
        _execute_runtime_sql(
            f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )

        factory = get_runtime_session_factory()

        with pytest.raises(RuntimeError, match="force rollback"), factory() as session:
            session.execute(
                text(f"INSERT INTO {table_name} (id, value) VALUES (1, 'rolled-back')")
            )
            raise RuntimeError("force rollback")

        with factory() as session:
            result = session.execute(text(f"SELECT COUNT(*) FROM {table_name}"))

        assert result.scalar_one() == 0
    finally:
        _execute_runtime_sql(f"DROP TABLE IF EXISTS {table_name}")


@requires_runtime_backend
def test_dispose_runtime_engine_cleans_up(runtime_database_url: str) -> None:
    init_runtime_engine(runtime_database_url)

    dispose_runtime_engine()

    with pytest.raises(RuntimeError, match="Runtime engine not initialized"):
        get_runtime_engine()


@requires_runtime_backend
def test_dual_engine_coexistence(
    managed_tmp_path: Path,
    runtime_database_url: str,
) -> None:
    original_data_dir = settings.data_dir
    settings.data_dir = managed_tmp_path / "anima-data"
    dispose_cached_engines()
    init_runtime_engine(runtime_database_url)

    request = _request_with_unlock_header()

    try:
        with patch(
            "anima_server.db.session.unlock_session_store.resolve",
            return_value=SimpleNamespace(user_id=123),
        ):
            db_dependency = get_db(request)
            soul_session = next(db_dependency)
            try:
                soul_result = soul_session.execute(text("SELECT 1")).scalar_one()
            finally:
                db_dependency.close()

        factory = get_runtime_session_factory()
        with factory() as runtime_session:
            runtime_result = runtime_session.execute(text("SELECT 1"))

        soul_engine = soul_session.get_bind()
        runtime_engine = get_runtime_engine()

        assert soul_result == 1
        assert runtime_result.scalar_one() == 1
        assert soul_engine.dialect.name == "sqlite"
        assert runtime_engine.dialect.name == "postgresql"
        assert soul_engine is not runtime_engine
    finally:
        settings.data_dir = original_data_dir
        dispose_cached_engines()


def test_config_auto_derives_url_from_embedded_pg(
    monkeypatch: pytest.MonkeyPatch,
    managed_tmp_path: Path,
) -> None:
    fake_pg = SimpleNamespace(
        database_url="postgresql://anima:test@localhost:5432/anima_runtime",
        stop=MagicMock(),
    )
    init_calls: list[tuple[str, bool]] = []
    cancel_pending_reflection = AsyncMock()
    drain_background_memory_tasks = AsyncMock()
    dispose_runtime_engine_mock = MagicMock()

    original_data_dir = settings.data_dir
    original_runtime_database_url = settings.runtime_database_url
    original_runtime_pg_data_dir = settings.runtime_pg_data_dir

    try:
        settings.data_dir = managed_tmp_path / "anima-data"
        settings.runtime_database_url = ""
        settings.runtime_pg_data_dir = ""
        dispose_cached_engines()
        main_module = _reload_main_module()

        monkeypatch.setattr(main_module, "_start_embedded_pg", lambda: fake_pg)
        monkeypatch.setattr(
            main_module,
            "init_runtime_engine",
            lambda database_url, *, echo=False, **kw: init_calls.append((database_url, echo)),
        )
        monkeypatch.setattr(main_module, "ensure_runtime_tables", lambda: None)
        monkeypatch.setattr(main_module, "dispose_runtime_engine", dispose_runtime_engine_mock)
        monkeypatch.setattr(
            "anima_server.services.agent.reflection.cancel_pending_reflection",
            cancel_pending_reflection,
        )
        monkeypatch.setattr(
            "anima_server.services.agent.consolidation.drain_background_memory_tasks",
            drain_background_memory_tasks,
        )

        app = main_module.create_app()

        with TestClient(app):
            assert init_calls == [(fake_pg.database_url, settings.database_echo)]

        cancel_pending_reflection.assert_awaited_once_with()
        drain_background_memory_tasks.assert_awaited_once_with()
        dispose_runtime_engine_mock.assert_called_once_with()
        fake_pg.stop.assert_called_once_with()
    finally:
        settings.data_dir = original_data_dir
        settings.runtime_database_url = original_runtime_database_url
        settings.runtime_pg_data_dir = original_runtime_pg_data_dir
        dispose_cached_engines()
        sys.modules.pop("anima_server.main", None)


def test_explicit_runtime_url_skips_embedded_pg(
    monkeypatch: pytest.MonkeyPatch,
    managed_tmp_path: Path,
) -> None:
    explicit_url = "postgresql://anima:test@localhost:5432/anima_runtime"
    init_calls: list[tuple[str, bool]] = []
    cancel_pending_reflection = AsyncMock()
    drain_background_memory_tasks = AsyncMock()
    dispose_runtime_engine_mock = MagicMock()

    original_data_dir = settings.data_dir
    original_runtime_database_url = settings.runtime_database_url
    original_runtime_pg_data_dir = settings.runtime_pg_data_dir

    try:
        settings.data_dir = managed_tmp_path / "anima-data"
        settings.runtime_database_url = explicit_url
        settings.runtime_pg_data_dir = ""
        dispose_cached_engines()
        main_module = _reload_main_module()

        assert main_module._start_embedded_pg() is None

        monkeypatch.setattr(
            main_module,
            "init_runtime_engine",
            lambda database_url, *, echo=False, **kw: init_calls.append((database_url, echo)),
        )
        monkeypatch.setattr(main_module, "ensure_runtime_tables", lambda: None)
        monkeypatch.setattr(main_module, "dispose_runtime_engine", dispose_runtime_engine_mock)
        monkeypatch.setattr(
            "anima_server.services.agent.reflection.cancel_pending_reflection",
            cancel_pending_reflection,
        )
        monkeypatch.setattr(
            "anima_server.services.agent.consolidation.drain_background_memory_tasks",
            drain_background_memory_tasks,
        )

        app = main_module.create_app()

        with TestClient(app):
            assert init_calls == [(explicit_url, settings.database_echo)]

        cancel_pending_reflection.assert_awaited_once_with()
        drain_background_memory_tasks.assert_awaited_once_with()
        dispose_runtime_engine_mock.assert_called_once_with()
    finally:
        settings.data_dir = original_data_dir
        settings.runtime_database_url = original_runtime_database_url
        settings.runtime_pg_data_dir = original_runtime_pg_data_dir
        dispose_cached_engines()
        sys.modules.pop("anima_server.main", None)


@pytest.mark.skipif(
    not importlib.util.find_spec("psycopg"),
    reason="psycopg (v3) not installed",
)
def test_init_runtime_engine_raises_on_invalid_url() -> None:
    init_runtime_engine("postgresql+psycopg://invalid:5432/nope")
    factory = get_runtime_session_factory()
    with pytest.raises((OSError, Exception)), factory() as session:
        session.execute(text("SELECT 1"))
