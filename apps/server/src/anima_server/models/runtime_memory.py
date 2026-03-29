"""PostgreSQL runtime models for the Soul Writer pipeline.

MemoryCandidate: extracted observations awaiting promotion to soul.
PromotionJournal: audit trail for Soul Writer decisions.
MemoryAccessLog: access tracking (replaces per-turn touch_memory_items writes to SQLCipher).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP as _PG_TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.runtime_base import RuntimeBase

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)


class MemoryCandidate(RuntimeBase):
    """Extracted observation awaiting promotion to SQLCipher soul."""

    __tablename__ = "memory_candidates"
    __table_args__ = (
        Index("ix_memory_candidates_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    importance_source: Mapped[str] = mapped_column(String(32), nullable=False, default="llm")
    supersedes_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_message_ids: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer).with_variant(Text, "sqlite"), nullable=True
    )
    extraction_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="extracted")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)


class PromotionJournal(RuntimeBase):
    """Audit trail for Soul Writer promotion decisions."""

    __tablename__ = "promotion_journal"
    __table_args__ = (
        Index("ix_promotion_journal_user", "user_id"),
        Index("ix_promotion_journal_hash", "content_hash", "decision"),
        Index("ix_promotion_journal_status", "journal_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pending_op_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_table: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    journal_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="tentative"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )


class MemoryAccessLog(RuntimeBase):
    """PG-side access tracking, replaces per-turn touch_memory_items SQLCipher writes."""

    __tablename__ = "memory_access_log"
    __table_args__ = (
        Index("ix_memory_access_log_user_item", "user_id", "memory_item_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    memory_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    accessed_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    synced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
