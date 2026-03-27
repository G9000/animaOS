"""Runtime-tier consciousness models (PostgreSQL).

Working cognition that is ephemeral - discarded on machine transfer,
rebuilt from seed values on next startup.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.runtime_base import RuntimeBase

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)


class WorkingContext(RuntimeBase):
    """Temporary per-session cognition for inner state and working memory."""

    __tablename__ = "working_context"
    __table_args__ = (
        UniqueConstraint("user_id", "section", name="uq_working_context_user_section"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ActiveIntention(RuntimeBase):
    """In-flight goals and behavioral rules."""

    __tablename__ = "active_intentions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CurrentEmotion(RuntimeBase):
    """Momentary emotional signal detected from a conversation turn."""

    __tablename__ = "current_emotions"
    __table_args__ = (
        Index("ix_current_emotions_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thread_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("runtime_threads.id", ondelete="SET NULL"),
        nullable=True,
    )
    emotion: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    evidence_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="linguistic",
    )
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trajectory: Mapped[str] = mapped_column(String(24), nullable=False, default="stable")
    previous_emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    topic: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    acted_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
