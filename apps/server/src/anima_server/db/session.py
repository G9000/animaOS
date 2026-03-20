from __future__ import annotations

import logging
import platform
from collections.abc import Generator
from threading import RLock

from fastapi import HTTPException, Request, status
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from pathlib import Path

from anima_server.config import settings
from anima_server.db.url import ensure_database_directory
from anima_server.services.sessions import get_sqlcipher_key, unlock_session_store
from anima_server.services.storage import get_user_data_dir

logger = logging.getLogger(__name__)

_engine_cache_lock = RLock()
_engine_cache: dict[str, Engine] = {}
_session_factory_cache: dict[str, sessionmaker[Session]] = {}
_user_engines: dict[str, Engine] = {}
_migrated_databases: set[str] = set()

_ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


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

    # Determine the SQLCipher raw key from one of three sources:
    #   1. ANIMA_CORE_PASSPHRASE env var → Argon2id + HKDF derivation
    #   2. Cached key from unified passphrase model (set after user login)
    #   3. None → plain SQLite (dev mode)
    raw_key: bytes | None = None

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
            eng = create_engine(
                url,
                echo=settings.database_echo,
                future=True,
                connect_args={"check_same_thread": False},
            )

            @event.listens_for(eng, "connect")
            def _set_sqlite_pragmas_fallback(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
                del connection_record
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode = WAL")
                cursor.execute("PRAGMA busy_timeout = 5000")
                cursor.close()

            return eng

        from anima_server.services.core import get_sqlcipher_kdf_salt
        from anima_server.services.crypto import derive_sqlcipher_key

        sqlcipher_salt = get_sqlcipher_kdf_salt()
        raw_key = derive_sqlcipher_key(passphrase, sqlcipher_salt)
    else:
        # Unified passphrase mode: check if a pre-derived key was cached
        # after user authentication (see db/user_store.py)
        raw_key = get_sqlcipher_key()

    if raw_key is not None:
        try:
            import sqlcipher3
        except ImportError:
            if require_encryption:
                raise RuntimeError(
                    "ANIMA_CORE_REQUIRE_ENCRYPTION is enabled but sqlcipher3 is not installed. "
                    "Install sqlcipher3 to enable database encryption: pip install sqlcipher3"
                )
            logger.warning(
                "sqlcipher3 not installed - falling back to unencrypted SQLite."
            )
            raw_key = None

    if raw_key is not None:
        hex_key = raw_key.hex()

        eng = create_engine(
            url,
            echo=settings.database_echo,
            future=True,
            module=sqlcipher3,  # type: ignore[possibly-undefined]
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(eng, "connect")
        def _set_sqlcipher_key(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
            del connection_record
            cursor = dbapi_connection.cursor()
            cursor.execute(f"""PRAGMA key = "x'{hex_key}'" """)
            cursor.execute("PRAGMA cipher_page_size = 4096")
            # PRAGMA cipher_memory_security = ON causes
            # STATUS_GUARD_PAGE_VIOLATION (0x80000001) on Windows
            # threads.  SQLCipher still zeroes sensitive memory on
            # deallocation without this pragma; enabling it adds
            # mlock/guard-page hardening that conflicts with Windows
            # thread stack management.
            if platform.system() != "Windows":
                cursor.execute("PRAGMA cipher_memory_security = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA busy_timeout = 5000")
            cursor.close()

        logger.info("Database encryption enabled (SQLCipher).")
        return eng

    # No SQLCipher key available — check if encryption is required
    from anima_server.services.core import has_wrapped_sqlcipher_key

    if require_encryption and not has_wrapped_sqlcipher_key():
        raise RuntimeError(
            "Encryption is required by default but no passphrase is configured.\n"
            "\n"
            "  Option 1: Set ANIMA_CORE_PASSPHRASE=<your-passphrase> to enable encryption.\n"
            "  Option 2: Set ANIMA_CORE_REQUIRE_ENCRYPTION=false to run without encryption\n"
            "            (development only — data will be stored in plaintext).\n"
            "\n"
            "The Core stores all memories, conversations, and identity data. Encryption\n"
            "protects this data at rest — it is strongly recommended for any non-dev use."
        )

    logger.info("Database encryption not configured - using plain SQLite.")
    eng = create_engine(
        url,
        echo=settings.database_echo,
        future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragmas_plain(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 5000")
        cursor.close()

    return eng


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
    engine_instance = get_engine(database_url)
    with _engine_cache_lock:
        _user_engines[database_url] = engine_instance
    factory = get_session_factory(database_url)
    if database_url not in _migrated_databases:
        _run_alembic_upgrade(engine_instance)
        _migrated_databases.add(database_url)
    return factory


def _run_alembic_upgrade(engine_instance: Engine) -> None:
    """Run Alembic migrations against a per-user engine."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect as sa_inspect

    cfg = Config(str(_ALEMBIC_INI))

    insp = sa_inspect(engine_instance)
    has_alembic = insp.has_table("alembic_version")
    has_app_tables = insp.has_table("users")

    with engine_instance.begin() as connection:
        cfg.attributes["connection"] = connection

        if has_app_tables and not has_alembic:
            # Legacy DB created by create_all — stamp at head so Alembic
            # considers it up-to-date (columns were already added manually).
            command.stamp(cfg, "head")
            logger.info("Stamped legacy database at Alembic head.")
        else:
            # Fresh DB: creates all tables via migration chain.
            # Existing tracked DB: applies only pending migrations.
            command.upgrade(cfg, "head")
            logger.info("Alembic upgrade complete.")


def get_user_session_factory(user_id: int) -> sessionmaker[Session]:
    return ensure_user_database(user_id)


def dispose_database(database_url: str) -> None:
    with _engine_cache_lock:
        factory = _session_factory_cache.pop(database_url, None)
        engine_instance = _engine_cache.pop(database_url, None)
        _user_engines.pop(database_url, None)
    _migrated_databases.discard(database_url)

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
        _user_engines.clear()
    _migrated_databases.clear()

    for _, engine_instance in engine_items:
        engine_instance.dispose()


def dispose_all_user_engines() -> None:
    with _engine_cache_lock:
        engine_items = list(_user_engines.items())
        _user_engines.clear()
        for database_url, _engine_instance in engine_items:
            _engine_cache.pop(database_url, None)
            _session_factory_cache.pop(database_url, None)
            _migrated_databases.discard(database_url)

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
