from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.runtime_base import RuntimeBase

TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)


class PendingMemoryOp(RuntimeBase):
    """Runtime-staged identity write awaiting consolidation into the soul store."""

    __tablename__ = "pending_memory_ops"
    __table_args__ = (
        Index("ix_pending_ops_user_pending", "user_id", "consolidated", "failed"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    op_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_block: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    old_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    consolidated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    consolidated_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    failed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
