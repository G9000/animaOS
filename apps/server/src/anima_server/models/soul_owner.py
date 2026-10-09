"""The opaque owner binding for one portable encrypted Soul."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.base import Base


class SoulOwner(Base):
    __tablename__ = "soul_owners"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    core_owner_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SoulSchemaArchive(Base):
    """Opaque evidence for the independently verified encrypted original."""

    __tablename__ = "soul_schema_archives"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_revision: Mapped[str] = mapped_column(String(32), nullable=False)
    source_inventory: Mapped[dict] = mapped_column(JSON, nullable=False)
    provenance_state: Mapped[str] = mapped_column(String(32), nullable=False)
    vacuum_completed: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
