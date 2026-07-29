from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from anima_server.config import get_runtime_settings_path, settings
from anima_server.db import dispose_cached_engines
from anima_server.db import runtime as runtime_module
from anima_server.db.pg_lifecycle import EmbeddedPG
from anima_server.db.runtime import (
    dispose_runtime_engine,
    get_runtime_engine,
    get_runtime_session_factory,
    init_runtime_engine,
)
from anima_server.db.session import get_db
from sqlalchemy import create_engine, inspect, text
from starlette.requests import Request

HAS_PGSERVER = importlib.util.find_spec("pgserver") is not None
HAS_PSYCOPG = importlib.util.find_spec(
    "psycopg") is not None or importlib.util.find_spec("psycopg2") is not None
EXPLICIT_RUNTIME_DATABASE_URL = os.getenv(
    "ANIMA_RUNTIME_DATABASE_URL", "").strip()

requires_embedded_pg = pytest.mark.skipif(
    bool(EXPLICIT_RUNTIME_DATABASE_URL) or not HAS_PGSERVER,
    reason=(
        "Embedded PostgreSQL tests require pgserver and are skipped when "
        "ANIMA_RUNTIME_DATABASE_URL points to an external PostgreSQL instance."
    ),
)
requires_runtime_backend = pytest.mark.skipif(
    not HAS_PSYCOPG or (
        not EXPLICIT_RUNTIME_DATABASE_URL and not HAS_PGSERVER),
    reason=(
        "Runtime PostgreSQL integration tests require psycopg plus either "
        "pgserver or ANIMA_RUNTIME_DATABASE_URL."
    ),
)


@pytest.fixture(autouse=True)
def _reset_runtime_engine_state(managed_tmp_path: Path) -> None:
    original_runtime_app_data_dir = settings.runtime_app_data_dir
    settings.runtime_app_data_dir = str(managed_tmp_path / "runtime-app-data")
    dispose_runtime_engine()
    yield
    dispose_runtime_engine()
    settings.runtime_app_data_dir = original_runtime_app_data_dir


@pytest.fixture
def runtime_database_url(managed_tmp_path: Path) -> str:
    if EXPLICIT_RUNTIME_DATABASE_URL:
        yield EXPLICIT_RUNTIME_DATABASE_URL
        return

    if not HAS_PGSERVER:
        pytest.skip(
            "pgserver is not installed and no external runtime database URL is configured.")

    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    _start_embedded_pg_or_skip(pg)
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


def _stub_create_app_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
    main_module,
) -> None:
    monkeypatch.setattr(main_module, "ensure_core_manifest", lambda: None)
    monkeypatch.setattr(main_module, "acquire_core_lock", lambda: True)
    monkeypatch.setattr(main_module, "load_persisted_runtime_settings", lambda: None)
    monkeypatch.setattr(main_module, "ensure_per_user_databases_ready", lambda: None)


def _run_app_lifespan(app) -> None:
    async def _run() -> None:
        async with app.router.lifespan_context(app):
            return None

    asyncio.run(_run())


def _start_embedded_pg_or_skip(pg: EmbeddedPG) -> None:
    try:
        pg.start()
    except RuntimeError as exc:
        pytest.skip(f"Embedded PostgreSQL is unavailable in this environment: {exc}")


@requires_embedded_pg
def test_embedded_pg_start_creates_data_directory(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")

    try:
        _start_embedded_pg_or_skip(pg)

        assert pg.data_dir.exists()
        assert pg.running is True
    finally:
        pg.stop()


@requires_embedded_pg
def test_embedded_pg_stop_is_idempotent(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    _start_embedded_pg_or_skip(pg)

    pg.stop()
    pg.stop()

    assert pg.running is False


@requires_embedded_pg
def test_embedded_pg_database_url_returns_raw_url(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")

    try:
        _start_embedded_pg_or_skip(pg)
        assert pg.database_url.startswith("postgresql")
    finally:
        pg.stop()


def test_embedded_pg_database_url_raises_when_not_running(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")

    with pytest.raises(RuntimeError, match="Embedded PG is not running"):
        _ = pg.database_url


def test_legacy_soul_database_migration_creates_missing_new_tables(
    managed_tmp_path: Path,
) -> None:
    from anima_server.db.session import _run_alembic_upgrade

    legacy_db = managed_tmp_path / "legacy-soul.db"
    engine = create_engine(f"sqlite:///{legacy_db.as_posix()}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username VARCHAR NOT NULL
                )
                """
            )
        )

    _run_alembic_upgrade(engine)

    inspector = inspect(engine)
    assert inspector.has_table("alembic_version")
    assert inspector.has_table("presence_configs")


def test_legacy_soul_database_migration_repairs_diary_entry_columns(
    managed_tmp_path: Path,
) -> None:
    from anima_server.db.session import _run_alembic_upgrade

    legacy_db = managed_tmp_path / "legacy-diary.db"
    engine = create_engine(f"sqlite:///{legacy_db.as_posix()}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username VARCHAR NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE diary_entries (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    entry_date VARCHAR(10) NOT NULL,
                    title TEXT,
                    body TEXT NOT NULL,
                    mood TEXT,
                    source VARCHAR(24) NOT NULL DEFAULT 'user',
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )

    _run_alembic_upgrade(engine)

    diary_columns = {column["name"] for column in inspect(engine).get_columns("diary_entries")}
    assert {"cover_attachment_id", "folder_id"}.issubset(diary_columns)


def test_stamped_soul_database_migration_repairs_missing_new_tables(
    managed_tmp_path: Path,
) -> None:
    from anima_server.db.session import _run_alembic_upgrade

    legacy_db = managed_tmp_path / "stamped-soul.db"
    engine = create_engine(f"sqlite:///{legacy_db.as_posix()}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username VARCHAR NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE alembic_version (
                    version_num VARCHAR(32) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
                """
            )
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES ('20260522_0001')")
        )

    _run_alembic_upgrade(engine)

    assert inspect(engine).has_table("presence_configs")


def test_runtime_alembic_has_single_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(runtime_module._ALEMBIC_RUNTIME_INI))
    heads = ScriptDirectory.from_config(cfg).get_heads()

    assert len(heads) == 1, f"runtime migrations must have one head, found {heads}"


def test_runtime_migration_repairs_missing_profile_candidates_after_bad_stamp(
    managed_tmp_path: Path,
) -> None:
    from alembic import command
    from alembic.config import Config

    runtime_db = managed_tmp_path / "stamped-runtime.db"
    engine = create_engine(f"sqlite:///{runtime_db.as_posix()}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE alembic_version (
                    version_num VARCHAR(255) NOT NULL,
                    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES ('020_merge_image_assets_candidate_salience')"
            )
        )

        cfg = Config(str(runtime_module._ALEMBIC_RUNTIME_INI))
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")

    inspector = inspect(engine)
    assert inspector.has_table("profile_update_candidates")
    assert {
        "ix_profile_update_candidates_hash",
        "ix_profile_update_candidates_user_status",
    }.issubset({index["name"] for index in inspector.get_indexes("profile_update_candidates")})


def test_user_profile_migration_sets_source_fks_null_on_delete(
    managed_tmp_path: Path,
) -> None:
    from anima_server.db.session import _run_alembic_upgrade

    legacy_db = managed_tmp_path / "profile-fks-soul.db"
    engine = create_engine(f"sqlite:///{legacy_db.as_posix()}", future=True)

    _run_alembic_upgrade(engine)

    inspector = inspect(engine)
    fields_fks = inspector.get_foreign_keys("user_profile_fields")
    evidence_fks = inspector.get_foreign_keys("user_profile_field_evidence")

    def ondelete_for(
        fks: list[dict[str, object]],
        *,
        constrained_column: str,
    ) -> str | None:
        for fk in fks:
            if fk.get("constrained_columns") == [constrained_column]:
                options = fk.get("options") or {}
                if isinstance(options, dict):
                    value = options.get("ondelete")
                    return str(value) if value is not None else None
        return None

    assert ondelete_for(fields_fks, constrained_column="source_memory_id") == "SET NULL"
    assert ondelete_for(fields_fks, constrained_column="source_evidence_id") == "SET NULL"
    assert ondelete_for(fields_fks, constrained_column="source_claim_evidence_id") == "SET NULL"
    assert ondelete_for(fields_fks, constrained_column="superseded_by_id") == "SET NULL"
    assert ondelete_for(fields_fks, constrained_column="user_id") == "CASCADE"
    assert ondelete_for(evidence_fks, constrained_column="source_memory_id") == "SET NULL"
    assert ondelete_for(evidence_fks, constrained_column="source_evidence_id") == "SET NULL"
    assert ondelete_for(evidence_fks, constrained_column="source_claim_evidence_id") == "SET NULL"
    assert ondelete_for(evidence_fks, constrained_column="user_id") == "CASCADE"


def test_legacy_soul_database_migration_repairs_existing_kg_columns(
    managed_tmp_path: Path,
) -> None:
    from anima_server.db.session import _run_alembic_upgrade
    from anima_server.models import KGEntity, KGRelation
    from sqlalchemy.orm import Session

    legacy_db = managed_tmp_path / "legacy-kg-soul.db"
    engine = create_engine(f"sqlite:///{legacy_db.as_posix()}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username VARCHAR NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE kg_entities (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    name VARCHAR(200) NOT NULL,
                    name_normalized VARCHAR(200) NOT NULL,
                    entity_type VARCHAR(50) NOT NULL DEFAULT 'unknown',
                    description TEXT NOT NULL DEFAULT '',
                    mentions INTEGER NOT NULL DEFAULT 1,
                    embedding_json JSON,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_kg_entities_user_name UNIQUE (user_id, name_normalized)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE kg_relations (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    source_id INTEGER NOT NULL,
                    destination_id INTEGER NOT NULL,
                    relation_type VARCHAR(100) NOT NULL,
                    mentions INTEGER NOT NULL DEFAULT 1,
                    source_memory_id INTEGER,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

    _run_alembic_upgrade(engine)

    inspector = inspect(engine)
    entity_columns = {column["name"] for column in inspector.get_columns("kg_entities")}
    relation_columns = {column["name"] for column in inspector.get_columns("kg_relations")}
    assert {"aliases_json", "embedding_checksum"}.issubset(entity_columns)
    assert {
        "evidence_id",
        "observed_at",
        "valid_from",
        "valid_to",
        "confidence",
        "status",
        "supersedes_relation_id",
        "evolves_from_relation_id",
    }.issubset(relation_columns)

    with Session(engine) as db:
        source = KGEntity(user_id=1, name="User", name_normalized="user", entity_type="person")
        destination = KGEntity(
            user_id=1,
            name="Acme",
            name_normalized="acme",
            entity_type="organization",
            aliases_json=["Acme Inc"],
        )
        db.add_all([source, destination])
        db.flush()
        relation = KGRelation(
            user_id=1,
            source_id=source.id,
            destination_id=destination.id,
            relation_type="works_at",
            status="active",
        )
        db.add(relation)
        db.flush()

        assert db.get(KGEntity, source.id).aliases_json is None
        assert db.get(KGRelation, relation.id).status == "active"


def test_legacy_kg_migration_downgrade_tolerates_missing_constraints(
    managed_tmp_path: Path,
) -> None:
    from alembic import command
    from alembic.config import Config
    from anima_server.db import session as session_module
    from anima_server.db.session import _run_alembic_upgrade

    legacy_db = managed_tmp_path / "legacy-kg-downgrade-soul.db"
    engine = create_engine(f"sqlite:///{legacy_db.as_posix()}", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    username VARCHAR NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE kg_entities (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    name VARCHAR(200) NOT NULL,
                    name_normalized VARCHAR(200) NOT NULL,
                    entity_type VARCHAR(50) NOT NULL DEFAULT 'unknown',
                    description TEXT NOT NULL DEFAULT '',
                    mentions INTEGER NOT NULL DEFAULT 1,
                    embedding_json JSON,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_kg_entities_user_name UNIQUE (user_id, name_normalized)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE kg_relations (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    source_id INTEGER NOT NULL,
                    destination_id INTEGER NOT NULL,
                    relation_type VARCHAR(100) NOT NULL,
                    mentions INTEGER NOT NULL DEFAULT 1,
                    source_memory_id INTEGER,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

    _run_alembic_upgrade(engine)

    cfg = Config(str(session_module._ALEMBIC_INI))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.downgrade(cfg, "20260626_0002")

    inspector = inspect(engine)
    entity_columns = {column["name"] for column in inspector.get_columns("kg_entities")}
    relation_columns = {column["name"] for column in inspector.get_columns("kg_relations")}
    assert "aliases_json" not in entity_columns
    assert "status" not in relation_columns


def test_embedded_pg_recovers_stale_lockfile(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    pg.data_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pg.data_dir / "postmaster.pid"
    pid_file.write_text("999999\n", encoding="utf-8")

    pg._recover_stale_lockfile()

    assert pid_file.exists() is False


def test_embedded_pg_keeps_valid_lockfile(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    pg.data_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pg.data_dir / "postmaster.pid"
    pid_file.write_text("42\n", encoding="utf-8")
    target = str(pg.data_dir.expanduser().resolve())

    class FakeProcess:
        def __init__(self) -> None:
            self.info = {
                "pid": 42,
                "name": "postgres.exe",
                "cmdline": ["postgres.exe", "-D", target],
            }

    fake_psutil = SimpleNamespace(
        NoSuchProcess=RuntimeError,
        AccessDenied=PermissionError,
        Error=RuntimeError,
        Process=lambda _pid: FakeProcess(),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(
        EmbeddedPG,
        "_probe_pid",
        staticmethod(lambda _pid: (True, False)),
    )

    pg._recover_stale_lockfile()

    assert pid_file.exists() is True


def test_embedded_pg_recovers_lockfile_when_pid_reused_by_non_postgres(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    pg.data_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pg.data_dir / "postmaster.pid"
    pid_file.write_text("42\n", encoding="utf-8")

    class FakeProcess:
        def __init__(self) -> None:
            self.info = {
                "pid": 42,
                "name": "python.exe",
                "cmdline": ["python", "lsp_server.py"],
            }

    fake_psutil = SimpleNamespace(
        NoSuchProcess=RuntimeError,
        AccessDenied=PermissionError,
        Error=RuntimeError,
        Process=lambda _pid: FakeProcess(),
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(
        EmbeddedPG,
        "_probe_pid",
        staticmethod(lambda _pid: (True, False)),
    )

    pg._recover_stale_lockfile()

    assert pid_file.exists() is False


def test_embedded_pg_keeps_lockfile_on_permission_error(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    pg.data_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pg.data_dir / "postmaster.pid"
    pid_file.write_text("42\n", encoding="utf-8")

    monkeypatch.setattr(
        EmbeddedPG,
        "_probe_pid",
        staticmethod(lambda _pid: (True, True)),
    )

    pg._recover_stale_lockfile()

    assert pid_file.exists() is True


def test_embedded_pg_bootstrap_log_is_outside_pgdata(managed_tmp_path: Path) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")

    assert pg._bootstrap_log_path() == managed_tmp_path / "runtime" / \
        f"pg_bootstrap_{os.getpid()}.log"


def test_embedded_pg_patches_pgserver_windows_startup(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anima_server.db.pg_lifecycle as pg_lifecycle_module

    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    pg.data_dir.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[list[str], Path | None, dict[str, object]]] = []

    def fake_pg_ctl(args: list[str], pgdata: Path | None = None, **kwargs: object) -> str:
        calls.append((list(args), pgdata, dict(kwargs)))
        return ""

    postgres_server_module = SimpleNamespace(pg_ctl=fake_pg_ctl)

    class FakePostgresServer:
        _instances: ClassVar[dict[Path, object]] = {}

        def __init__(self) -> None:
            self.pgdata = pg.data_dir
            self.log = self.pgdata / "log"

        def ensure_postgres_running(self) -> None:
            postgres_server_module.pg_ctl(
                [
                    "-w",
                    "-o",
                    '-h "127.0.0.1"',
                    "-o",
                    "-p 55432",
                    "-l",
                    str(self.log),
                    "start",
                ],
                pgdata=self.pgdata,
                timeout=10,
            )

    fake_pgserver = SimpleNamespace(PostgresServer=FakePostgresServer)
    monkeypatch.setattr(pg_lifecycle_module.os, "name", "nt", raising=False)
    monkeypatch.setitem(
        sys.modules, "pgserver.postgres_server", postgres_server_module)

    pg._patch_pgserver_windows_startup(fake_pgserver)

    server = FakePostgresServer()
    server.ensure_postgres_running()

    assert server.log == pg._bootstrap_log_path()
    assert len(calls) == 1
    assert calls[0][1] == pg.data_dir
    assert calls[0][2]["timeout"] == 60.0
    assert calls[0][0][calls[0][0].index(
        "-l") + 1] == str(pg._bootstrap_log_path())


def test_embedded_pg_terminates_postgres_processes_for_data_dir(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    target = str(pg.data_dir.expanduser().resolve())
    terminated: list[int] = []
    killed: list[int] = []

    class FakeTimeoutExpired(Exception):
        pass

    class FakeProcess:
        def __init__(self, pid: int, name: str, cmdline: list[str], *, times_out: bool = False) -> None:
            self.info = {"pid": pid, "name": name, "cmdline": cmdline}
            self._times_out = times_out

        def terminate(self) -> None:
            terminated.append(self.info["pid"])

        def wait(self, _timeout: int) -> None:
            if self._times_out:
                raise FakeTimeoutExpired

        def kill(self) -> None:
            killed.append(self.info["pid"])

    fake_psutil = SimpleNamespace(
        TimeoutExpired=FakeTimeoutExpired,
        Error=RuntimeError,
        process_iter=lambda attrs=None: [
            FakeProcess(1, "postgres", ["postgres", "-D", target]),
            FakeProcess(2, "postgres", ["postgres",
                        "-D", target], times_out=True),
            FakeProcess(3, "postgres", ["postgres", "-D", "C:/other/pg_data"]),
            FakeProcess(4, "python", ["python", "app.py"]),
        ],
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    pg._terminate_postgres_processes_for_data_dir()

    assert terminated == [1, 2]
    assert killed == [2]


def test_embedded_pg_start_discards_cached_unready_pgserver_instance(
    managed_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pg = EmbeddedPG(managed_tmp_path / "runtime" / "pg_data")
    instance_key = pg.data_dir.expanduser().resolve()

    class BrokenServer:
        def __init__(self) -> None:
            self.cleaned = False

        def get_uri(self) -> str:
            raise AssertionError("_postmaster_info is missing")

        def cleanup(self) -> None:
            self.cleaned = True

    class ReadyServer:
        def get_uri(self) -> str:
            return "postgresql://127.0.0.1:5432/postgres"

    class FakePostgresServer:
        _instances: ClassVar[dict[Path, object]] = {}

    broken = BrokenServer()
    ready = ReadyServer()
    FakePostgresServer._instances[instance_key] = broken

    def fake_get_server(pgdata: str, cleanup_mode: str = "stop") -> object:
        del cleanup_mode
        resolved = Path(pgdata).expanduser().resolve()
        cached = FakePostgresServer._instances.get(resolved)
        if cached is not None:
            return cached
        FakePostgresServer._instances[resolved] = ready
        return ready

    monkeypatch.setitem(
        sys.modules,
        "pgserver",
        SimpleNamespace(
            get_server=fake_get_server,
            PostgresServer=FakePostgresServer,
        ),
    )

    pg.start()

    assert pg.running is True
    assert pg.database_url == "postgresql://127.0.0.1:5432/postgres"
    assert broken.cleaned is True
    assert FakePostgresServer._instances[instance_key] is ready


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
                text(
                    f"INSERT INTO {table_name} (id, value) VALUES (1, 'committed')")
            )
            session.commit()

        with factory() as session:
            result = session.execute(
                text(f"SELECT value FROM {table_name} WHERE id = 1"))

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
                text(
                    f"INSERT INTO {table_name} (id, value) VALUES (1, 'rolled-back')")
            )
            raise RuntimeError("force rollback")

        with factory() as session:
            result = session.execute(
                text(f"SELECT COUNT(*) FROM {table_name}"))

        assert result.scalar_one() == 0
    finally:
        _execute_runtime_sql(f"DROP TABLE IF EXISTS {table_name}")


@requires_runtime_backend
def test_dispose_runtime_engine_cleans_up(runtime_database_url: str) -> None:
    init_runtime_engine(runtime_database_url)

    dispose_runtime_engine()

    with pytest.raises(RuntimeError, match="Runtime engine not initialized"):
        get_runtime_engine()


def test_ensure_pgvector_enables_vector_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = MagicMock()
    context_manager = MagicMock()
    context_manager.__enter__.return_value = connection
    context_manager.__exit__.return_value = None
    engine = MagicMock()
    engine.begin.return_value = context_manager

    monkeypatch.setattr(runtime_module, "get_runtime_engine", lambda: engine)
    monkeypatch.setattr(
        runtime_module,
        "get_runtime_engine_name",
        lambda: runtime_module.RuntimeDatabaseEngine.POSTGRES,
    )

    runtime_module.ensure_pgvector()

    statement = connection.execute.call_args.args[0]
    assert str(statement) == "CREATE EXTENSION IF NOT EXISTS vector"


def test_ensure_pgvector_logs_warning_when_extension_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = MagicMock()
    engine.begin.side_effect = RuntimeError("pgvector unavailable")
    warning = MagicMock()

    monkeypatch.setattr(runtime_module, "get_runtime_engine", lambda: engine)
    monkeypatch.setattr(
        runtime_module,
        "get_runtime_engine_name",
        lambda: runtime_module.RuntimeDatabaseEngine.POSTGRES,
    )
    monkeypatch.setattr(runtime_module.logger, "warning", warning)

    runtime_module.ensure_pgvector()

    warning.assert_called_once()
    assert "pgvector extension not available" in warning.call_args.args[0]


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
                soul_result = soul_session.execute(
                    text("SELECT 1")).scalar_one()
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
            lambda database_url, *, echo=False, **kw: init_calls.append(
                (database_url, echo)),
        )
        monkeypatch.setattr(main_module, "ensure_pgvector", lambda: None)
        monkeypatch.setattr(main_module, "ensure_runtime_tables", lambda: None)
        monkeypatch.setattr(
            main_module, "dispose_runtime_engine", dispose_runtime_engine_mock)
        monkeypatch.setattr(
            "anima_server.services.agent.reflection.cancel_pending_reflection",
            cancel_pending_reflection,
        )
        monkeypatch.setattr(
            "anima_server.services.agent.consolidation.drain_background_memory_tasks",
            drain_background_memory_tasks,
        )
        _stub_create_app_bootstrap(monkeypatch, main_module)

        app = main_module.create_app()

        _run_app_lifespan(app)
        assert init_calls == [
            (fake_pg.database_url, settings.database_echo)]

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


def test_embedded_runtime_claim_is_resolved_before_postgres_starts(
    monkeypatch: pytest.MonkeyPatch,
    managed_tmp_path: Path,
) -> None:
    app_data = managed_tmp_path / "machine-app-data"
    observed: list[tuple[str, Path]] = []
    original_data_dir = settings.data_dir
    original_runtime_database_url = settings.runtime_database_url
    original_runtime_pg_data_dir = settings.runtime_pg_data_dir
    original_runtime_app_data_dir = settings.runtime_app_data_dir
    original_runtime_instance_data_dir = settings.runtime_instance_data_dir
    original_health_log_dir = settings.health_log_dir

    class FakeEmbeddedPG:
        def __init__(self, data_dir: Path) -> None:
            self.data_dir = data_dir

        def start(self) -> None:
            registry_path = app_data / "core-instance-registry.json"
            assert registry_path.is_file()
            observed.append(("start", self.data_dir))

        def stop(self) -> None:
            return None

    try:
        settings.data_dir = managed_tmp_path / "portable" / ".anima"
        settings.runtime_database_url = ""
        settings.runtime_pg_data_dir = ""
        settings.runtime_app_data_dir = str(app_data)
        dispose_cached_engines()
        main_module = _reload_main_module()
        monkeypatch.setattr(main_module, "EmbeddedPG", FakeEmbeddedPG)

        pg = main_module._start_embedded_pg()

        assert pg is not None
        registry = json.loads(
            (app_data / "core-instance-registry.json").read_text(encoding="utf-8")
        )
        local_instance_id = registry["instances"][0]["local_instance_id"]
        assert observed == [
            (
                "start",
                app_data
                / "cores"
                / registry["instances"][0]["core_id"]
                / "instances"
                / local_instance_id
                / "runtime"
                / "pg_data",
            )
        ]
        assert not observed[0][1].is_relative_to(settings.data_dir)
        assert Path(settings.runtime_instance_data_dir) == (
            app_data
            / "cores"
            / registry["instances"][0]["core_id"]
            / "instances"
            / local_instance_id
        )
        assert Path(settings.health_log_dir) == (
            Path(settings.runtime_instance_data_dir) / "health-logs"
        )
        assert get_runtime_settings_path() == (
            Path(settings.runtime_instance_data_dir)
            / "config"
            / "runtime-config.json"
        )
        assert not get_runtime_settings_path().is_relative_to(settings.data_dir)
        main_module._release_runtime_instance_claim()
    finally:
        settings.data_dir = original_data_dir
        settings.runtime_database_url = original_runtime_database_url
        settings.runtime_pg_data_dir = original_runtime_pg_data_dir
        settings.runtime_app_data_dir = original_runtime_app_data_dir
        settings.runtime_instance_data_dir = original_runtime_instance_data_dir
        settings.health_log_dir = original_health_log_dir
        dispose_cached_engines()
        sys.modules.pop("anima_server.main", None)


def test_embedded_runtime_reuses_relocated_legacy_pg_until_cutover(
    monkeypatch: pytest.MonkeyPatch,
    managed_tmp_path: Path,
) -> None:
    app_data = managed_tmp_path / "machine-app-data"
    core = managed_tmp_path / "portable" / ".anima"
    core.mkdir(parents=True)
    (core / "manifest.json").write_text(
        json.dumps({"core_id": "core-legacy-runtime"}),
        encoding="utf-8",
    )
    legacy_pg = core / "runtime" / "pg_data"
    legacy_pg.mkdir(parents=True)
    (legacy_pg / "PG_VERSION").write_text("17", encoding="ascii")
    observed: list[Path] = []

    class FakeEmbeddedPG:
        def __init__(self, data_dir: Path) -> None:
            self.data_dir = data_dir

        def start(self) -> None:
            observed.append(self.data_dir)

        def stop(self) -> None:
            return None

    original_data_dir = settings.data_dir
    original_runtime_database_url = settings.runtime_database_url
    original_runtime_pg_data_dir = settings.runtime_pg_data_dir
    original_runtime_app_data_dir = settings.runtime_app_data_dir
    original_health_log_dir = settings.health_log_dir

    try:
        settings.data_dir = core
        settings.runtime_database_url = ""
        settings.runtime_pg_data_dir = ""
        settings.runtime_app_data_dir = str(app_data)
        settings.health_log_dir = ""
        main_module = _reload_main_module()
        monkeypatch.setattr(main_module, "EmbeddedPG", FakeEmbeddedPG)

        pg = main_module._start_embedded_pg()

        assert pg is not None
        registry = json.loads(
            (app_data / "core-instance-registry.json").read_text(encoding="utf-8")
        )
        record = registry["instances"][0]
        assert observed == [
            app_data
            / "cores"
            / record["core_id"]
            / "instances"
            / record["local_instance_id"]
            / "legacy-runtime-source"
            / "pg_data"
        ]
        assert not legacy_pg.exists()
        main_module._release_runtime_instance_claim()
    finally:
        settings.data_dir = original_data_dir
        settings.runtime_database_url = original_runtime_database_url
        settings.runtime_pg_data_dir = original_runtime_pg_data_dir
        settings.runtime_app_data_dir = original_runtime_app_data_dir
        settings.health_log_dir = original_health_log_dir
        dispose_cached_engines()
        sys.modules.pop("anima_server.main", None)


def test_explicit_runtime_url_skips_embedded_pg(
    monkeypatch: pytest.MonkeyPatch,
    managed_tmp_path: Path,
) -> None:
    explicit_url = "postgresql://anima:test@localhost:5432/anima_runtime"
    init_calls: list[tuple[str, bool]] = []
    startup_order: list[str] = []
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

        real_claim_runtime_instance = main_module._claim_runtime_instance
        monkeypatch.setattr(
            main_module,
            "_claim_runtime_instance",
            lambda *, runtime_url=None: (
                startup_order.append(f"claim:{runtime_url}"),
                real_claim_runtime_instance(runtime_url=runtime_url),
            )[1],
        )
        monkeypatch.setattr(
            main_module,
            "init_runtime_engine",
            lambda database_url, *, echo=False, **kw: (
                startup_order.append(f"init:{database_url}"),
                init_calls.append((database_url, echo)),
            ),
        )
        monkeypatch.setattr(
            main_module,
            "ensure_runtime_database_binding",
            lambda *, core_id, local_instance_id: startup_order.append(
                f"bind:{core_id}:{local_instance_id}"
            ),
        )
        monkeypatch.setattr(main_module, "ensure_pgvector", lambda: None)
        monkeypatch.setattr(main_module, "ensure_runtime_tables", lambda: None)
        monkeypatch.setattr(
            main_module, "dispose_runtime_engine", dispose_runtime_engine_mock)
        monkeypatch.setattr(
            "anima_server.services.agent.reflection.cancel_pending_reflection",
            cancel_pending_reflection,
        )
        monkeypatch.setattr(
            "anima_server.services.agent.consolidation.drain_background_memory_tasks",
            drain_background_memory_tasks,
        )
        _stub_create_app_bootstrap(monkeypatch, main_module)

        app = main_module.create_app()

        _run_app_lifespan(app)
        assert init_calls == [(explicit_url, settings.database_echo)]
        assert startup_order[:2] == [
            f"claim:{explicit_url}",
            f"init:{explicit_url}",
        ]
        assert startup_order[2].startswith("bind:")

        cancel_pending_reflection.assert_awaited_once_with()
        drain_background_memory_tasks.assert_awaited_once_with()
        dispose_runtime_engine_mock.assert_called_once_with()
    finally:
        settings.data_dir = original_data_dir
        settings.runtime_database_url = original_runtime_database_url
        settings.runtime_pg_data_dir = original_runtime_pg_data_dir
        dispose_cached_engines()
        sys.modules.pop("anima_server.main", None)


def test_lifespan_shutdown_closes_unlock_store_before_runtime_disposal_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    managed_tmp_path: Path,
) -> None:
    shutdown_order: list[str] = []
    original_data_dir = settings.data_dir
    original_runtime_database_url = settings.runtime_database_url
    original_background_memory_enabled = settings.agent_background_memory_enabled

    async def cancelled_reflection_drain() -> None:
        shutdown_order.append("reflection")
        raise asyncio.CancelledError

    async def shutdown_unlock_store() -> None:
        shutdown_order.append("unlock-store")

    try:
        settings.data_dir = managed_tmp_path / "anima-data"
        settings.runtime_database_url = ""
        settings.agent_background_memory_enabled = False
        main_module = _reload_main_module()

        import anima_server.services.sessions as sessions_module

        monkeypatch.setattr(main_module, "_start_embedded_pg", lambda: None)
        monkeypatch.setattr(
            main_module,
            "dispose_runtime_engine",
            lambda: shutdown_order.append("runtime"),
        )
        monkeypatch.setattr(
            "anima_server.services.agent.reflection.cancel_pending_reflection",
            cancelled_reflection_drain,
        )
        monkeypatch.setattr(
            "anima_server.services.agent.fastembed_backend.warm_up_retrieval_models",
            lambda: None,
        )
        monkeypatch.setattr(sessions_module.unlock_session_store, "start", lambda: None)
        monkeypatch.setattr(
            sessions_module.unlock_session_store,
            "shutdown",
            shutdown_unlock_store,
        )
        _stub_create_app_bootstrap(monkeypatch, main_module)

        app = main_module.create_app()

        with pytest.raises(asyncio.CancelledError):
            _run_app_lifespan(app)

        assert shutdown_order == ["reflection", "unlock-store", "runtime"]
    finally:
        settings.data_dir = original_data_dir
        settings.runtime_database_url = original_runtime_database_url
        settings.agent_background_memory_enabled = original_background_memory_enabled
        dispose_cached_engines()
        sys.modules.pop("anima_server.main", None)


@pytest.mark.parametrize("failure_stage", ["embedded-postgres", "runtime-migration"])
def test_lifespan_startup_failure_closes_unlock_store_before_runtime_resources(
    monkeypatch: pytest.MonkeyPatch,
    managed_tmp_path: Path,
    failure_stage: str,
) -> None:
    shutdown_order: list[str] = []
    original_data_dir = settings.data_dir
    original_runtime_database_url = settings.runtime_database_url

    class FakeEmbeddedPG:
        database_url = "postgresql://anima:test@localhost:5432/anima_runtime"

        def stop(self) -> None:
            shutdown_order.append("embedded-postgres")

    def start_embedded_pg() -> FakeEmbeddedPG:
        if failure_stage == "embedded-postgres":
            raise RuntimeError("startup failure")
        return FakeEmbeddedPG()

    async def shutdown_unlock_store() -> None:
        shutdown_order.append("unlock-store")

    def ensure_runtime_tables() -> None:
        if failure_stage == "runtime-migration":
            raise RuntimeError("startup failure")

    try:
        settings.data_dir = managed_tmp_path / "anima-data"
        settings.runtime_database_url = ""
        dispose_cached_engines()
        main_module = _reload_main_module()

        import anima_server.services.sessions as sessions_module

        monkeypatch.setattr(main_module, "_start_embedded_pg", start_embedded_pg)
        monkeypatch.setattr(main_module, "init_runtime_engine", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(main_module, "ensure_pgvector", lambda: None)
        monkeypatch.setattr(main_module, "ensure_runtime_tables", ensure_runtime_tables)
        monkeypatch.setattr(
            main_module,
            "dispose_runtime_engine",
            lambda: shutdown_order.append("runtime"),
        )
        monkeypatch.setattr(sessions_module.unlock_session_store, "start", lambda: None)
        monkeypatch.setattr(
            sessions_module.unlock_session_store,
            "shutdown",
            shutdown_unlock_store,
        )
        _stub_create_app_bootstrap(monkeypatch, main_module)

        app = main_module.create_app()

        with pytest.raises(RuntimeError, match="startup failure"):
            _run_app_lifespan(app)

        expected = ["unlock-store", "runtime"]
        if failure_stage == "runtime-migration":
            expected.append("embedded-postgres")
        assert shutdown_order == expected
    finally:
        settings.data_dir = original_data_dir
        settings.runtime_database_url = original_runtime_database_url
        dispose_cached_engines()
        sys.modules.pop("anima_server.main", None)


def test_runtime_startup_enables_pgvector_before_runtime_migrations(
    monkeypatch: pytest.MonkeyPatch,
    managed_tmp_path: Path,
) -> None:
    explicit_url = "postgresql://anima:test@localhost:5432/anima_runtime"
    startup_calls: list[str] = []
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

        monkeypatch.setattr(
            main_module,
            "init_runtime_engine",
            lambda database_url, *, echo=False, **kw: startup_calls.append(
                f"init:{database_url}"
            ),
        )
        monkeypatch.setattr(
            main_module,
            "ensure_runtime_database_binding",
            lambda *, core_id, local_instance_id: startup_calls.append(
                f"bind:{core_id}:{local_instance_id}"
            ),
            raising=False,
        )
        monkeypatch.setattr(
            main_module,
            "ensure_pgvector",
            lambda: startup_calls.append("pgvector"),
            raising=False,
        )
        monkeypatch.setattr(
            main_module,
            "ensure_runtime_tables",
            lambda: startup_calls.append("migrate"),
        )
        monkeypatch.setattr(
            main_module, "dispose_runtime_engine", dispose_runtime_engine_mock)
        monkeypatch.setattr(
            "anima_server.services.agent.reflection.cancel_pending_reflection",
            cancel_pending_reflection,
        )
        monkeypatch.setattr(
            "anima_server.services.agent.consolidation.drain_background_memory_tasks",
            drain_background_memory_tasks,
        )
        _stub_create_app_bootstrap(monkeypatch, main_module)

        app = main_module.create_app()

        _run_app_lifespan(app)
        registry = json.loads(
            (
                Path(settings.runtime_app_data_dir)
                / "core-instance-registry.json"
            ).read_text(encoding="utf-8")
        )
        binding_record = registry["instances"][0]
        assert startup_calls == [
            f"init:{explicit_url}",
            (
                f"bind:{binding_record['core_id']}:"
                f"{binding_record['local_instance_id']}"
            ),
            "pgvector",
            "migrate",
        ]
        cancel_pending_reflection.assert_awaited_once_with()
        drain_background_memory_tasks.assert_awaited_once_with()
        dispose_runtime_engine_mock.assert_called_once_with()
    finally:
        settings.data_dir = original_data_dir
        settings.runtime_database_url = original_runtime_database_url
        settings.runtime_pg_data_dir = original_runtime_pg_data_dir
        dispose_cached_engines()
        sys.modules.pop("anima_server.main", None)


def test_runtime_database_binding_rejects_another_core_instance(
    managed_tmp_path: Path,
) -> None:
    runtime_db = managed_tmp_path / "shared-runtime.db"
    init_runtime_engine(f"sqlite+pysqlite:///{runtime_db.as_posix()}")

    runtime_module.ensure_runtime_database_binding(
        core_id="core-a",
        local_instance_id="instance-a",
    )
    runtime_module.ensure_runtime_database_binding(
        core_id="core-a",
        local_instance_id="instance-a",
    )

    with pytest.raises(RuntimeError, match="another Core instance"):
        runtime_module.ensure_runtime_database_binding(
            core_id="core-b",
            local_instance_id="instance-b",
        )

    with get_runtime_engine().connect() as connection:
        row = connection.execute(
            text(
                "SELECT core_id, local_instance_id "
                "FROM corefs_runtime_binding WHERE binding_slot = 1"
            )
        ).one()
    assert row == ("core-a", "instance-a")


@pytest.mark.skipif(
    not importlib.util.find_spec("psycopg"),
    reason="psycopg (v3) not installed",
)
def test_init_runtime_engine_raises_on_invalid_url() -> None:
    init_runtime_engine("postgresql+psycopg://invalid:5432/nope")
    factory = get_runtime_session_factory()
    with pytest.raises((OSError, Exception)), factory() as session:
        session.execute(text("SELECT 1"))
