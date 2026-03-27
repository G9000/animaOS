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

from sqlalchemy import create_engine
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
