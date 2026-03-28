"""PostgreSQL-native runtime models.

These mirror the soul models in ``agent_runtime.py`` but target PostgreSQL
via :class:`RuntimeBase` instead of the per-user SQLCipher :class:`Base`.

Key differences from the soul models:
- ``BigInteger`` primary keys
- ``TIMESTAMP(timezone=True)`` instead of ``DateTime(timezone=True)``
- ``postgresql.JSON`` instead of generic ``JSON``
- Table names prefixed with ``runtime_``
- ``user_id`` is a plain indexed column (no FK to soul tables)
- ForeignKeys within runtime tables ARE enforced
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.dialects.postgresql import TIMESTAMP as _PG_TIMESTAMP

# TIMESTAMPTZ shorthand — ``TIMESTAMP(timezone=True)`` is the portable
# spelling that works across all SQLAlchemy versions & PG backends.
TIMESTAMPTZ = _PG_TIMESTAMP(timezone=True)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from anima_server.db.runtime_base import RuntimeBase


class RuntimeThread(RuntimeBase):
    __tablename__ = "runtime_threads"
    __table_args__ = (
        Index("ix_runtime_threads_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    next_message_sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    messages: Mapped[list[RuntimeMessage]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="RuntimeMessage.sequence_id",
    )
    runs: Mapped[list[RuntimeRun]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="RuntimeRun.started_at",
    )


class RuntimeRun(RuntimeBase):
    __tablename__ = "runtime_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_approval_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
    )

    thread: Mapped[RuntimeThread] = relationship(back_populates="runs")
    steps: Mapped[list[RuntimeStep]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RuntimeStep.step_index",
    )


class RuntimeStep(RuntimeBase):
    __tablename__ = "runtime_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_index", name="uq_runtime_steps_run_id_step_index"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    request_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    response_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    tool_calls_json: Mapped[list[dict[str, object]] | None] = mapped_column(
        JSON, nullable=True
    )
    usage_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[RuntimeRun] = relationship(back_populates="steps")


class RuntimeMessage(RuntimeBase):
    __tablename__ = "runtime_messages"
    __table_args__ = (
        UniqueConstraint(
            "thread_id", "sequence_id", name="uq_runtime_messages_thread_id_sequence_id"
        ),
        Index("ix_runtime_messages_user_created", "user_id", "created_at"),
        Index("ix_runtime_messages_thread_context", "thread_id", "is_in_context"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("runtime_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("runtime_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    step_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("runtime_steps.id", ondelete="SET NULL"),
        nullable=True,
    )
    sequence_id: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool_args_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    is_in_context: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    token_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )

    thread: Mapped[RuntimeThread] = relationship(back_populates="messages")


class RuntimeBackgroundTaskRun(RuntimeBase):
    """Tracked background task execution for debugging and monitoring."""

    __tablename__ = "runtime_background_task_runs"
    __table_args__ = (
        Index("ix_runtime_bg_task_runs_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
    )
    task_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # consolidation, graph_ingestion, heat_decay, episode_gen, etc.
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
    )  # pending, running, completed, failed
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
