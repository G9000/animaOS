"""Synchronous PostgreSQL engine for the Runtime store.

Uses psycopg (v3) as the DBAPI driver.  The entire service layer is
synchronous today (async conversion is planned for P7), so we use plain
:class:`~sqlalchemy.engine.Engine` / :class:`~sqlalchemy.orm.Session`
instead of the async equivalents.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

_runtime_engine: Engine | None = None
_runtime_session_factory: sessionmaker[Session] | None = None

_ALEMBIC_RUNTIME_INI = Path(__file__).resolve().parents[3] / "alembic_runtime.ini"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _to_sync_url(url: str) -> str:
    """Convert any PostgreSQL URL to ``postgresql+psycopg://`` format."""
    if "+psycopg" in url:
        return url
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg", 1)
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


# ---------------------------------------------------------------------------
# Engine lifecycle
# ---------------------------------------------------------------------------

def init_runtime_engine(
    database_url: str,
    *,
    echo: bool = False,
    pool_size: int = 5,
    max_overflow: int = 10,
) -> None:
    """Create the Runtime store sync engine and session factory."""
    global _runtime_engine, _runtime_session_factory

    sync_url = _to_sync_url(database_url)

    _runtime_engine = create_engine(
        sync_url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )
    _runtime_session_factory = sessionmaker(
        bind=_runtime_engine,
        autoflush=False,
        expire_on_commit=False,
    )


def dispose_runtime_engine() -> None:
    """Dispose the Runtime store engine (synchronous)."""
    global _runtime_engine, _runtime_session_factory

    if _runtime_engine is not None:
        _runtime_engine.dispose()
        _runtime_engine = None
        _runtime_session_factory = None


def get_runtime_engine() -> Engine:
    """Return the Runtime store engine; raises if not initialised."""
    if _runtime_engine is None:
        raise RuntimeError(
            "Runtime engine not initialized. "
            "Call init_runtime_engine() during server startup."
        )
    return _runtime_engine


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


# ---------------------------------------------------------------------------
# Alembic migration helper
# ---------------------------------------------------------------------------

def ensure_runtime_tables() -> None:
    """Run Alembic runtime migrations programmatically."""
    from alembic import command
    from alembic.config import Config

    engine = get_runtime_engine()
    cfg = Config(str(_ALEMBIC_RUNTIME_INI))

    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")

    logger.info("Runtime Alembic migrations applied.")

    _reconcile_embedding_dimension(engine)


def _reconcile_embedding_dimension(engine: Engine) -> None:
    """Drop and recreate the embeddings table if the vector dimension changed.

    The PG embeddings table is a runtime cache — source of truth is
    ``MemoryItem.embedding_json`` in SQLite.  Safe to recreate; a
    background sync will repopulate it.

    Uses ``create_all`` (not Alembic) to recreate the table, because
    Alembic's ``upgrade head`` is a no-op when already at head.  The
    column type on the ORM model is updated in-place before creating
    so the new table gets the correct dimension.
    """
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
                "Embedding dimension mismatch: PG column has %d, "
                "model expects %d — recreating embeddings table",
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
        logger.info(
            "Embeddings table recreated with dimension %d. "
            "Background sync will repopulate.",
            expected_dim,
        )
    except Exception:
        logger.debug("Embedding dimension check skipped", exc_info=True)


def ensure_pgvector() -> None:
    """Enable the pgvector extension. Idempotent."""
    engine = get_runtime_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        logger.info("pgvector extension enabled.")
    except Exception:
        logger.warning(
            "pgvector extension not available. "
            "Vector search will use in-memory fallback."
        )
