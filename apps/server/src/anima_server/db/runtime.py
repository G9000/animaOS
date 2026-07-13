"""Runtime store bootstrap for PostgreSQL and local Turso-style engines."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Generator
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, func, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


class RuntimeDatabaseEngine(StrEnum):
    """Supported Runtime engine values.

    - "postgres": PostgreSQL via psycopg-compatible SQLAlchemy URL
    - "turso": local Turso-style SQLite runtime database
    """

    POSTGRES = "postgres"
    TURSO = "turso"


_runtime_engine: Engine | None = None
_runtime_session_factory: sessionmaker[Session] | None = None
_runtime_engine_name: RuntimeDatabaseEngine | None = None

_ALEMBIC_RUNTIME_INI = Path(__file__).resolve().parents[3] / "alembic_runtime.ini"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _to_sync_postgres_url(url: str) -> str:
    """Convert any PostgreSQL URL to ``postgresql+psycopg://`` format."""
    if "+psycopg" in url:
        return url
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg", 1)
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _normalize_turso_url(database_url: str) -> str:
    """Return a SQLAlchemy URL for Turso-style file-backed runtime stores."""
    candidate = (database_url or "").strip()
    if not candidate:
        return ""

    if candidate.startswith("sqlite:"):
        return candidate

    if "://" in candidate:
        return candidate

    return f"sqlite:///{Path(candidate).resolve()}"


def _coerce_engine(name: RuntimeDatabaseEngine | str | None) -> RuntimeDatabaseEngine:
    if isinstance(name, RuntimeDatabaseEngine):
        return name
    normalized = (name or "").strip().lower()
    if normalized == "turso":
        return RuntimeDatabaseEngine.TURSO
    return RuntimeDatabaseEngine.POSTGRES


def _configure_turso_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA busy_timeout = 30000")
        with contextlib.suppress(Exception):
            cursor.execute("PRAGMA synchronous = NORMAL")
    finally:
        cursor.close()


def _is_runtime_busy_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "locked" in message
        or "busy" in message
        or "database is locked" in message
        or "database is busy" in message
    )


# ---------------------------------------------------------------------------
# Engine lifecycle
# ---------------------------------------------------------------------------

def init_runtime_engine(
    database_url: str,
    *,
    engine: RuntimeDatabaseEngine | str = RuntimeDatabaseEngine.POSTGRES,
    echo: bool = False,
    pool_size: int = 5,
    max_overflow: int = 10,
) -> None:
    """Create the Runtime store sync engine and session factory."""
    global _runtime_engine, _runtime_session_factory, _runtime_engine_name

    runtime_engine = _coerce_engine(engine)

    if runtime_engine == RuntimeDatabaseEngine.TURSO:
        sync_url = _normalize_turso_url(database_url)
        if not sync_url:
            raise ValueError("Turso runtime requires a database path or URL.")
        _runtime_engine = create_engine(
            sync_url,
            echo=echo,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30.0},
            pool_size=pool_size,
            max_overflow=max_overflow,
        )
        event.listen(_runtime_engine, "connect", _configure_turso_connection)
    else:
        sync_url = _to_sync_postgres_url(database_url)
        _runtime_engine = create_engine(
            sync_url,
            echo=echo,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
        )

    _runtime_engine_name = runtime_engine
    _runtime_session_factory = sessionmaker(
        bind=_runtime_engine,
        autoflush=False,
        expire_on_commit=False,
    )


def dispose_runtime_engine() -> None:
    """Dispose the Runtime store engine (synchronous)."""
    global _runtime_engine, _runtime_session_factory, _runtime_engine_name

    if _runtime_engine is not None:
        _runtime_engine.dispose()
        _runtime_engine = None
        _runtime_session_factory = None
        _runtime_engine_name = None


def get_runtime_engine() -> Engine:
    """Return the Runtime store engine; raises if not initialised."""
    if _runtime_engine is None:
        raise RuntimeError(
            "Runtime engine not initialized. "
            "Call init_runtime_engine() during server startup."
        )
    return _runtime_engine


def get_runtime_engine_name() -> RuntimeDatabaseEngine:
    """Return the active Runtime engine mode; raises if uninitialised."""
    if _runtime_engine_name is None:
        raise RuntimeError(
            "Runtime engine not initialized. "
            "Call init_runtime_engine() during server startup."
        )
    return _runtime_engine_name


def get_runtime_session_factory() -> sessionmaker[Session]:
    """Return the Runtime store session factory."""
    if _runtime_session_factory is None:
        raise RuntimeError("Runtime session factory not initialized.")
    return _runtime_session_factory


def get_runtime_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a Runtime session.

    Commits on success, rolls back on exception, always closes.
    """
    if _runtime_session_factory is None:
        raise RuntimeError("Runtime session factory not initialized.")

    session = _runtime_session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_runtime_transaction(
    callback: Callable[[Session], Any],
    *,
    retries: int = 5,
    base_delay: float = 0.05,
) -> Any:
    """Execute a write callback in a short transaction with busy-retry support."""
    if retries < 1:
        retries = 1

    last_error: BaseException | None = None
    for attempt in range(retries):
        session = get_runtime_session_factory()()
        try:
            value = callback(session)
            session.commit()
            return value
        except Exception as exc:
            session.rollback()
            last_error = exc
            if attempt + 1 >= retries or not _is_runtime_busy_error(exc):
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                "Runtime write conflict on attempt %d/%d; retrying after %.3fs: %s",
                attempt + 1,
                retries,
                delay,
                exc,
            )
            time.sleep(delay)
        finally:
            session.close()

    if last_error is not None:
        raise RuntimeError("Runtime transaction exhausted retries") from last_error
    return None


# ---------------------------------------------------------------------------
# Alembic migration helper
# ---------------------------------------------------------------------------

def ensure_runtime_tables() -> None:
    """Run Runtime migrations for the active engine."""
    runtime_engine = get_runtime_engine()
    if get_runtime_engine_name() == RuntimeDatabaseEngine.POSTGRES:
        _ensure_runtime_tables_postgres(runtime_engine)
    else:
        _ensure_runtime_tables_turso(runtime_engine)


def _ensure_runtime_tables_postgres(engine: Engine) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_RUNTIME_INI))

    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")

    logger.info("Runtime Alembic migrations applied.")
    _reconcile_embedding_dimension(engine)


def _ensure_runtime_tables_turso(engine: Engine) -> None:
    """Bootstrap Turso-compatible runtime schema from ORM metadata."""
    from anima_server.db.runtime_base import RuntimeBase
    from anima_server.models import pending_memory_op as _pending_memory_op  # noqa: F401
    from anima_server.models import runtime as _runtime_models  # noqa: F401
    from anima_server.models import runtime_consciousness as _runtime_consciousness  # noqa: F401
    from anima_server.models import runtime_embedding as _runtime_embedding  # noqa: F401
    from anima_server.models import runtime_memory as _runtime_memory  # noqa: F401

    RuntimeBase.metadata.create_all(bind=engine)
    logger.info("Runtime schema created for Turso-compatible engine.")


def _reconcile_embedding_dimension(engine: Engine) -> None:
    """Drop and recreate the embeddings table if the vector dimension changed."""

    from anima_server.config import resolve_embedding_dim

    expected_dim = resolve_embedding_dim()

    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'embeddings'::regclass "
                    "AND attname = 'embedding'"
                )
            ).fetchone()
            if row is None:
                return
            pg_dim = row[0]
            if pg_dim == expected_dim:
                return
            logger.warning(
                "Embedding dimension mismatch: PG column has %d, model expects %d - "
                "recreating embeddings table",
                pg_dim,
                expected_dim,
            )

        from pgvector.sqlalchemy import Vector

        from anima_server.db.runtime_base import RuntimeBase
        from anima_server.models.runtime_embedding import RuntimeEmbedding

        RuntimeEmbedding.__table__.c.embedding.type = Vector(expected_dim)

        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS embeddings CASCADE"))
        RuntimeBase.metadata.create_all(
            engine, tables=[RuntimeEmbedding.__table__]
        )
        _mark_indexed_documents_unindexed_after_embedding_reset(engine)
        logger.info(
            "Embeddings table recreated with dimension %d. "
            "Background sync will repopulate.",
            expected_dim,
        )
    except Exception:
        logger.debug("Embedding dimension check skipped", exc_info=True)


def _mark_indexed_documents_unindexed_after_embedding_reset(engine: Engine) -> int:
    """Clear indexed state for documents with resumable workflows."""
    from anima_server.models.runtime import (
        RuntimeDocument,
        RuntimeDocumentChunk,
        RuntimeWorkflowRun,
    )

    chunk_exists = (
        select(RuntimeDocumentChunk.id)
        .where(
            RuntimeDocumentChunk.document_id == RuntimeDocument.id,
            RuntimeDocumentChunk.user_id == RuntimeDocument.user_id,
        )
        .exists()
    )
    resumable_workflow_exists = (
        select(RuntimeWorkflowRun.id)
        .where(
            RuntimeWorkflowRun.id == RuntimeDocument.workflow_run_id,
            RuntimeWorkflowRun.user_id == RuntimeDocument.user_id,
            ~RuntimeWorkflowRun.status.in_(("completed", "failed", "cancelled")),
        )
        .exists()
    )
    stmt = (
        update(RuntimeDocument)
        .where(
            RuntimeDocument.status == "indexed",
            chunk_exists,
            resumable_workflow_exists,
        )
        .values(
            status="registered",
            indexed_at=None,
            updated_at=func.now(),
        )
    )
    with engine.begin() as conn:
        result = conn.execute(stmt)

    marked = int(result.rowcount or 0)
    if marked:
        logger.info(
            "Marked %d resumable indexed document(s) unindexed after embeddings reset.",
            marked,
        )
    return marked


def ensure_pgvector() -> None:
    """Enable the pgvector extension when using PostgreSQL."""
    if get_runtime_engine_name() != RuntimeDatabaseEngine.POSTGRES:
        logger.debug("Skipping pgvector enablement because runtime engine is not PostgreSQL.")
        return

    engine = get_runtime_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        logger.info("pgvector extension enabled.")
    except Exception:
        logger.warning(
            "pgvector extension not available. "
            "Vector search will use non-pg fallback."
        )
