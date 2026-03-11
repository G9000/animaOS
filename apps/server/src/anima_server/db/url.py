from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url


def ensure_database_directory(database_url: str) -> None:
    url = make_url(database_url)

    if not url.drivername.startswith("sqlite"):
        return

    database = url.database
    if not database or database == ":memory:":
        return

    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
