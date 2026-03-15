from __future__ import annotations

import logging
from collections.abc import Generator
from threading import RLock

from fastapi import HTTPException, Request, status
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from anima_server.config import settings
from anima_server.db.base import Base
from anima_server.db.url import ensure_database_directory
from anima_server.services.sessions import unlock_session_store
from anima_server.services.storage import get_user_data_dir

logger = logging.getLogger(__name__)

_engine_cache_lock = RLock()
_engine_cache: dict[str, Engine] = {}
_session_factory_cache: dict[str, sessionmaker[Session]] = {}


def _make_engine(database_url: str | None = None) -> Engine:
    url = database_url or settings.database_url
    ensure_database_directory(url)

    if not url.startswith("sqlite"):
        return create_engine(
            url,
            echo=settings.database_echo,
            future=True,
            pool_pre_ping=True,
        )

    passphrase = settings.core_passphrase.strip()
    require_encryption = settings.core_require_encryption

    if require_encryption and not passphrase:
        raise RuntimeError(
            "ANIMA_CORE_REQUIRE_ENCRYPTION is enabled but ANIMA_CORE_PASSPHRASE is not set. "
            "Provide a passphrase or disable encryption enforcement."
        )

    if passphrase:
        try:
            import sqlcipher3
        except ImportError:
            if require_encryption:
                raise RuntimeError(
                    "ANIMA_CORE_REQUIRE_ENCRYPTION is enabled but sqlcipher3 is not installed. "
                    "Install sqlcipher3 to enable database encryption: pip install sqlcipher3"
                )
            logger.warning(
                "sqlcipher3 not installed - falling back to unencrypted SQLite. "
                "Install sqlcipher3 to enable database encryption."
            )
            return create_engine(
                url,
                echo=settings.database_echo,
                future=True,
                connect_args={"check_same_thread": False},
            )

        eng = create_engine(
            url,
            echo=settings.database_echo,
            future=True,
            module=sqlcipher3,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(eng, "connect")
        def _set_sqlcipher_key(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
            del connection_record
            cursor = dbapi_connection.cursor()
            escaped = passphrase.replace("'", "''")
            cursor.execute(f"PRAGMA key = '{escaped}'")
            cursor.close()

        logger.info("Database encryption enabled (SQLCipher).")
        return eng

    if require_encryption:
        raise RuntimeError("Encryption required but no passphrase provided.")

    logger.info("Database encryption not configured - using plain SQLite.")
    return create_engine(
        url,
        echo=settings.database_echo,
        future=True,
        connect_args={"check_same_thread": False},
    )


def get_engine(database_url: str) -> Engine:
    cached = _engine_cache.get(database_url)
    if cached is not None:
        return cached

    with _engine_cache_lock:
        cached = _engine_cache.get(database_url)
        if cached is not None:
            return cached

        engine_instance = _make_engine(database_url)
        _engine_cache[database_url] = engine_instance
        return engine_instance


def get_session_factory(database_url: str) -> sessionmaker[Session]:
    cached = _session_factory_cache.get(database_url)
    if cached is not None:
        return cached

    with _engine_cache_lock:
        cached = _session_factory_cache.get(database_url)
        if cached is not None:
            return cached

        factory = sessionmaker(
            bind=get_engine(database_url),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
        _session_factory_cache[database_url] = factory
        return factory


def get_user_database_path(user_id: int):
    return get_user_data_dir(user_id) / "anima.db"


def is_sqlite_mode() -> bool:
    """Return True when the server is configured for per-user SQLite databases."""
    return make_url(settings.database_url).drivername.startswith("sqlite")


def get_user_database_url(user_id: int) -> str:
    if not is_sqlite_mode():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Per-user database routing requires SQLite mode. "
            "Shared-database deployments must not silently fall back to "
            "the shared URL — that would break tenant isolation.",
        )

    path = get_user_database_path(user_id).resolve()
    return f"sqlite:///{path.as_posix()}"


def ensure_user_database(user_id: int) -> sessionmaker[Session]:
    database_url = get_user_database_url(user_id)
    factory = get_session_factory(database_url)
    engine_instance = get_engine(database_url)
    Base.metadata.create_all(bind=engine_instance)
    return factory


def get_user_session_factory(user_id: int) -> sessionmaker[Session]:
    return ensure_user_database(user_id)


def dispose_database(database_url: str) -> None:
    with _engine_cache_lock:
        factory = _session_factory_cache.pop(database_url, None)
        engine_instance = _engine_cache.pop(database_url, None)

    del factory
    if engine_instance is not None:
        engine_instance.dispose()


def dispose_user_database(user_id: int) -> None:
    dispose_database(get_user_database_url(user_id))


def dispose_cached_engines() -> None:
    with _engine_cache_lock:
        engine_items = list(_engine_cache.items())
        _engine_cache.clear()
        _session_factory_cache.clear()

    for _, engine_instance in engine_items:
        engine_instance.dispose()


engine = get_engine(settings.database_url)
SessionLocal = get_session_factory(settings.database_url)


def get_db(request: Request) -> Generator[Session, None, None]:
    token = request.headers.get("x-anima-unlock")
    session = unlock_session_store.resolve(token.strip() if token else None)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session locked. Please sign in again.",
        )

    db = get_user_session_factory(session.user_id)()
    try:
        yield db
    finally:
        db.close()


def build_session_factory_for_db(db: Session) -> sessionmaker[Session]:
    bind = db.get_bind()
    resolved_bind = getattr(bind, "engine", bind)
    return sessionmaker(
        bind=resolved_bind,
        autoflush=db.autoflush,
        expire_on_commit=db.expire_on_commit,
        class_=type(db),
    )
