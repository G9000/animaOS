from __future__ import annotations
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy import BigInteger, create_engine, event
from fastapi.testclient import TestClient
from anima_server.services.sessions import clear_sqlcipher_key, unlock_session_store
from anima_server.services.agent.vector_store import reset_vector_store
from anima_server.services.agent import invalidate_agent_runtime_cache
from anima_server.db.runtime_base import RuntimeBase
from anima_server.db import runtime as runtime_mod
from anima_server.db import dispose_cached_engines
from anima_server.config import settings
import pytest

import os
import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

# Disable encryption requirement for tests (must be set before settings import).
os.environ.setdefault("ANIMA_CORE_REQUIRE_ENCRYPTION", "false")


from anima_server.models import runtime as _runtime_models  # noqa: F401 — register tables
from anima_server.models import runtime_consciousness as _runtime_consciousness_models  # noqa: F401
from anima_server.models import (
    runtime_memory as _runtime_memory_models,  # noqa: F401 — register runtime_session_notes
)

# ---------------------------------------------------------------------------
# SQLite compat: BigInteger → INTEGER so AUTOINCREMENT works for runtime models.
# Runtime models use BigInteger PKs (designed for PostgreSQL).  SQLite maps
# BigInteger to BIGINT/NUMERIC which breaks AUTOINCREMENT.  This override
# ensures BigInteger emits plain INTEGER on SQLite.
# ---------------------------------------------------------------------------


@compiles(BigInteger, "sqlite")
def _compile_biginteger_sqlite(type_: BigInteger, compiler: object, **kw: object) -> str:
    return "INTEGER"


@pytest.fixture(autouse=True)
def _init_runtime_engine_for_tests() -> Generator[None, None, None]:
    """Auto-init the runtime module globals so get_runtime_session_factory() works.

    Creates a lightweight in-memory SQLite engine with runtime tables and
    patches the module-level singletons so any code path that calls
    ``get_runtime_session_factory()`` (e.g. ``_build_runtime_db_factory()``
    inside ``run_agent``) gets a working factory without needing PostgreSQL.
    """
    # If the globals are already set (e.g. by test_runtime_db.py which manages
    # its own engine lifecycle), skip this fixture.
    if runtime_mod._runtime_engine is not None:
        yield
        return

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: object, connection_record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 30000")
        cursor.close()

    RuntimeBase.metadata.create_all(engine)

    runtime_mod._runtime_engine = engine
    runtime_mod._runtime_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    yield

    runtime_mod._runtime_engine = None
    runtime_mod._runtime_session_factory = None
    engine.dispose()


def _resolve_test_temp_root() -> Path:
    override = os.environ.get("ANIMA_TEST_TEMP_ROOT")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / "anima-tests"


TEST_TEMP_ROOT = _resolve_test_temp_root()


def create_managed_temp_dir(prefix: str) -> Path:
    TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    temp_root = TEST_TEMP_ROOT / f"{prefix}{uuid4().hex}"
    temp_root.mkdir(parents=True, exist_ok=False)
    return temp_root


@pytest.fixture()
def managed_tmp_path() -> Generator[Path, None, None]:
    temp_root = create_managed_temp_dir("anima-test-")
    reset_vector_store()
    try:
        yield temp_root
    finally:
        reset_vector_store()
        shutil.rmtree(temp_root, ignore_errors=True)


@contextmanager
def managed_test_client(
    prefix: str,
    *,
    invalidate_agent: bool = True,
) -> Generator[TestClient, None, None]:
    temp_root = create_managed_temp_dir(prefix)
    original_data_dir = settings.data_dir

    settings.data_dir = temp_root / "anima-data"
    dispose_cached_engines()
    unlock_session_store.clear()
    clear_sqlcipher_key()
    reset_vector_store()
    if invalidate_agent:
        invalidate_agent_runtime_cache()

    # Import lazily so pytest collection does not initialize the app
    # against the developer data directory.
    import anima_server.main as main_module

    app = main_module.create_app()

    try:
        with patch.object(main_module, "_start_embedded_pg", return_value=None), TestClient(app) as client:
            yield client
    finally:
        unlock_session_store.clear()
        clear_sqlcipher_key()
        reset_vector_store()
        dispose_cached_engines()
        settings.data_dir = original_data_dir
        if invalidate_agent:
            invalidate_agent_runtime_cache()
        shutil.rmtree(temp_root, ignore_errors=True)
