from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from anima_server.db.base import Base


class AgentThread(Base):
    __tablename__ = "agent_threads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    messages: Mapped[list["AgentMessage"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="AgentMessage.sequence_id",
    )
    runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="AgentRun.started_at",
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    stop_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    thread: Mapped[AgentThread] = relationship(back_populates="runs")
    steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AgentStep.step_index",
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_index", name="uq_agent_steps_run_id_step_index"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    request_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    response_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    tool_calls_json: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    usage_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[AgentRun] = relationship(back_populates="steps")


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence_id", name="uq_agent_messages_thread_id_sequence_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=True,
    )
    step_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_steps.id", ondelete="SET NULL"),
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    thread: Mapped[AgentThread] = relationship(back_populates="messages")


class MemoryItem(Base):
    __tablename__ = "memory_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(
        String(24), nullable=False,
    )  # fact, preference, goal, relationship, focus
    importance: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3,
    )  # 1-5
    source: Mapped[str] = mapped_column(
        String(24), nullable=False, default="extraction",
    )  # extraction, user, reflection
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("memory_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_referenced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    reference_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MemoryEpisode(Base):
    __tablename__ = "memory_episodes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="SET NULL"),
        nullable=True,
    )
    date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    time: Mapped[str | None] = mapped_column(String(8), nullable=True)  # HH:MM:SS
    topics_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    emotional_arc: Mapped[str | None] = mapped_column(String(128), nullable=True)
    significance_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3,
    )  # 1-5
    turn_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MemoryDailyLog(Base):
    __tablename__ = "memory_daily_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_response: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
