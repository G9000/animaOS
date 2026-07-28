from __future__ import annotations

import os

# Disable encryption requirement for tests (must be set before settings import).
os.environ.setdefault("ANIMA_CORE_REQUIRE_ENCRYPTION", "false")

# Keep the suite hermetic to the developer's local embedding provider: a
# machine-specific ANIMA_AGENT_EMBEDDING_PROVIDER / _MODEL (e.g. an ollama
# config in .env.local) otherwise leaks through pydantic-settings into every
# test — and, because models/runtime_embedding.py resolves the Vector column
# dimension from the configured model AT IMPORT TIME, even changes the
# embeddings table shape the suite runs against (768 for nomic-embed-text vs
# 384 for the bundled bge-small default). Real environment variables beat
# .env file values in pydantic-settings; tests that need a different provider
# set it explicitly (monkeypatch), which still wins. Must be set before the
# anima_server imports below instantiate settings.
os.environ["ANIMA_AGENT_EMBEDDING_PROVIDER"] = "fastembed"
os.environ["ANIMA_AGENT_EMBEDDING_MODEL"] = ""

# Same hermeticity requirement for the core passphrase: a developer's
# ANIMA_CORE_PASSPHRASE (shell env or .env.local) flips the server into
# env-passphrase mode, and registration then SKIPS the versioned key-hierarchy
# provisioning (`_maybe_generate_sqlcipher_key` returns None) — which silently
# failed ~54 CoreFS/keyslots/recovery/vault tests for months as a "pre-existing
# baseline" (MIH-003). Tests assume unified (wrapped-key) mode; the handful
# that exercise passphrase mode set `settings.core_passphrase` explicitly via
# monkeypatch, which still wins over this.
os.environ["ANIMA_CORE_PASSPHRASE"] = ""

# Fail fast (with an actionable message) when the installed anima_core native
# module is stale relative to the checkout: a missing symbol otherwise surfaces
# as dozens of cryptic AttributeErrors deep inside the CoreFS suites (the other
# half of the MIH-003 "pre-existing baseline"). CorefsSession is the symbol
# services/sessions.py hard-requires.
import anima_core as _anima_core

if not hasattr(_anima_core, "CorefsSession"):
    raise RuntimeError(
        "The installed anima_core native module is stale (missing CorefsSession). "
        "Rebuild it from the checkout: uv sync --reinstall-package anima-core"
    )

import shutil
import sys
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from anima_server.config import settings
from anima_server.db import dispose_cached_engines
from anima_server.db import runtime as runtime_mod
from anima_server.db.runtime_base import RuntimeBase
from anima_server.services.agent import fastembed_backend as fastembed_backend_module
from anima_server.services.agent import invalidate_agent_runtime_cache
from anima_server.services.agent.vector_store import reset_vector_store
from anima_server.services.documents import reranker as reranker_module
from anima_server.services.sessions import clear_sqlcipher_key, unlock_session_store
from fastapi.testclient import TestClient
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Tests exercise the deterministic knowledge compiler by default; LLM-path
# tests opt in explicitly with a scripted client.
settings.knowledge_compiler = "deterministic"


def _reranker_model_unavailable_in_tests() -> Any:
    # Belt-and-suspenders: retrieval_reranker defaults to "local" in
    # production, and rag.py's rerank call site has no per-call override, so
    # any test exercising the real search path would otherwise reach here
    # and download the ONNX model over the network. Tests that want real
    # reranker behavior monkeypatch ``reranker._create_model`` themselves
    # (see test_contextual_rerank.py); everything else must degrade to the
    # fused order exactly as it would if the model failed to load.
    raise RuntimeError("Reranker model construction is stubbed out in tests")


reranker_module._create_model = _reranker_model_unavailable_in_tests


def _fastembed_model_unavailable_in_tests(model_name: str) -> Any:
    # Same belt-and-suspenders as the reranker stub above: fastembed is now
    # the bundled default embedding provider, so any test exercising the real
    # embedding path would otherwise reach here and download the ONNX model
    # (~130MB) over the network mid-suite. Tests that want real fastembed
    # behavior monkeypatch ``fastembed_backend._create_model`` (or
    # ``embed_texts``) themselves (see test_fastembed_backend.py); everything
    # else must degrade to the same "embedding unavailable" behavior as if
    # the model failed to load.
    raise RuntimeError("Fastembed model construction is stubbed out in tests")


fastembed_backend_module._create_model = _fastembed_model_unavailable_in_tests


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
    return Path(__file__).resolve().parents[3] / ".tmp-tests"


TEST_TEMP_ROOT = _resolve_test_temp_root()
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)

# Python's tempfile defaults use %TEMP% on Windows. In some dev shells, Python
# 0o700 temp directories become ACL-broken and unreadable by the creating
# process. Route test temp creation through the managed root used by this suite.
tempfile.tempdir = str(TEST_TEMP_ROOT)
os.environ.setdefault("TMP", str(TEST_TEMP_ROOT))
os.environ.setdefault("TEMP", str(TEST_TEMP_ROOT))


def create_managed_temp_dir(prefix: str) -> Path:
    TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    temp_root = TEST_TEMP_ROOT / f"{prefix}{uuid4().hex}"
    temp_root.mkdir(parents=True, exist_ok=False)
    return temp_root


@pytest.fixture()
def tmp_path() -> Generator[Path, None, None]:
    temp_root = create_managed_temp_dir("pytest-tmp-")
    try:
        yield temp_root
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


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
    unlock_session_store.start()
    unlock_session_store.clear()
    clear_sqlcipher_key()
    reset_vector_store()
    if invalidate_agent:
        invalidate_agent_runtime_cache()

    # Import lazily so pytest collection does not initialize the app
    # against the developer data directory.
    sys.modules.pop("anima_server.main", None)

    with (
        patch("anima_server.services.core.ensure_core_manifest", lambda: None),
        patch("anima_server.services.core.acquire_core_lock", lambda: True),
        patch("anima_server.config.load_persisted_runtime_settings", lambda: None),
        patch("anima_server.db.user_store.ensure_per_user_databases_ready", lambda: None),
    ):
        import anima_server.main as main_module

        app = main_module.create_app()

    try:
        with patch.object(main_module, "_start_embedded_pg", return_value=None), TestClient(app) as client:
            yield client
    finally:
        unlock_session_store.clear()
        unlock_session_store.start()
        clear_sqlcipher_key()
        reset_vector_store()
        dispose_cached_engines()
        settings.data_dir = original_data_dir
        if invalidate_agent:
            invalidate_agent_runtime_cache()
        sys.modules.pop("anima_server.main", None)
        shutil.rmtree(temp_root, ignore_errors=True)
