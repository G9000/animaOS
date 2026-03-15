"""FastAPI dependency that gates endpoints to SQLite (per-user) mode only."""
from __future__ import annotations

from fastapi import HTTPException, status

from anima_server.db.session import is_sqlite_mode


def require_sqlite_mode() -> None:
    """Raise 403 when the server is not running in per-user SQLite mode.

    Use as a FastAPI dependency *before* ``get_db`` so that requests are
    rejected with a clear 403 instead of failing during DB-session
    creation.
    """
    if not is_sqlite_mode():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is disabled in shared-database mode.",
        )
