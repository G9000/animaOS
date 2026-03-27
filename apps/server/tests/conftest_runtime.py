"""Runtime database test fixtures.

Provides a ``runtime_db`` fixture backed by an in-process SQLite database.
This covers basic CRUD operations; PG-specific features (TIMESTAMPTZ, FOR
UPDATE, etc.) are tested in integration tests against a real PG instance.

Because the runtime models use ``BigInteger`` primary keys (which SQLite
maps to BIGINT / NUMERIC affinity rather than INTEGER), we register a
compiler override so that ``BigInteger`` emits ``INTEGER`` on SQLite,
allowing ``AUTOINCREMENT`` to work correctly.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import pytest
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from anima_server.db.runtime_base import RuntimeBase
from anima_server.models import runtime as _runtime_models  # noqa: F401 — register tables


# ---------------------------------------------------------------------------
# SQLite compat: BigInteger → INTEGER so AUTOINCREMENT works
# ---------------------------------------------------------------------------

@compiles(BigInteger, "sqlite")
def _compile_biginteger_sqlite(type_: BigInteger, compiler: object, **kw: object) -> str:
    return "INTEGER"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runtime_engine() -> Generator[Engine, None, None]:
    """SQLite engine with runtime tables for unit tests."""
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
    yield engine
    engine.dispose()


@pytest.fixture()
def runtime_db(runtime_engine: Engine) -> Generator[Session, None, None]:
    """Yield a runtime session backed by in-memory SQLite."""
    factory = sessionmaker(
        bind=runtime_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def runtime_session_factory(runtime_engine: Engine) -> sessionmaker[Session]:
    """Return a sessionmaker bound to the runtime test engine."""
    return sessionmaker(
        bind=runtime_engine,
        autoflush=False,
        expire_on_commit=False,
    )


# ---------------------------------------------------------------------------
# Context manager (for tests that don't use fixtures)
# ---------------------------------------------------------------------------


@contextmanager
def runtime_db_session() -> Generator[Session, None, None]:
    """Standalone context manager that provides a runtime session.

    Use this in test files that create their own DB sessions with
    ``_db_session()`` context managers rather than pytest fixtures.
    """
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
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    session = factory()
    try:
        yield session
    finally:
        session.close()
        RuntimeBase.metadata.drop_all(engine)
        engine.dispose()
