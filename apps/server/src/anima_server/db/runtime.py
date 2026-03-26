from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_runtime_engine: AsyncEngine | None = None
_runtime_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_runtime_engine(database_url: str, *, echo: bool = False) -> None:
    """Initialize the Runtime store async engine."""
    global _runtime_engine, _runtime_session_factory

    _runtime_engine = create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )
    _runtime_session_factory = async_sessionmaker(
        bind=_runtime_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def dispose_runtime_engine() -> None:
    """Dispose the Runtime store async engine."""
    global _runtime_engine, _runtime_session_factory

    if _runtime_engine is not None:
        await _runtime_engine.dispose()
        _runtime_engine = None
        _runtime_session_factory = None


def get_runtime_engine() -> AsyncEngine:
    """Return the Runtime store async engine."""
    if _runtime_engine is None:
        raise RuntimeError(
            "Runtime engine not initialized. "
            "Call init_runtime_engine() during server startup."
        )
    return _runtime_engine


@asynccontextmanager
async def get_runtime_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session for the Runtime store."""
    if _runtime_session_factory is None:
        raise RuntimeError("Runtime session factory not initialized.")

    async with _runtime_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
