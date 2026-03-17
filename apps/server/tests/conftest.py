from __future__ import annotations

import os
# Disable encryption requirement for tests (must be set before settings import).
os.environ.setdefault("ANIMA_CORE_REQUIRE_ENCRYPTION", "false")

import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from anima_server.config import settings
from anima_server.db import dispose_cached_engines
from anima_server.services.agent import invalidate_agent_runtime_cache
from anima_server.services.agent.vector_store import reset_vector_store
from anima_server.services.sessions import clear_sqlcipher_key, unlock_session_store


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
    from anima_server.main import create_app

    app = create_app()

    try:
        with TestClient(app) as client:
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
