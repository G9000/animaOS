"""Import SQLAlchemy models here so Alembic can discover metadata."""

from anima_server.db.base import Base

__all__ = ["Base"]
