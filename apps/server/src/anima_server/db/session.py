from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from anima_server.config import settings
from anima_server.db.url import ensure_database_directory

logger = logging.getLogger(__name__)

ensure_database_directory(settings.database_url)


def _make_engine() -> Engine:
    url = settings.database_url

    if not url.startswith("sqlite"):
        return create_engine(
            url,
            echo=settings.database_echo,
            future=True,
            pool_pre_ping=True,
        )

    passphrase = settings.core_passphrase.strip()

    if passphrase:
        import sqlcipher3

        eng = create_engine(
            url,
            echo=settings.database_echo,
            future=True,
            module=sqlcipher3,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(eng, "connect")
        def _set_sqlcipher_key(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA key = ?", (passphrase,))
            cursor.close()

        return eng

    return create_engine(
        url,
        echo=settings.database_echo,
        future=True,
        connect_args={"check_same_thread": False},
    )


engine = _make_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
