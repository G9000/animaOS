from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
    next_message_sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )

    messages: Mapped[list[AgentMessage]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="AgentMessage.sequence_id",
    )
    runs: Mapped[list[AgentRun]] = relationship(
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
    pending_approval_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    thread: Mapped[AgentThread] = relationship(back_populates="runs")
    steps: Mapped[list[AgentStep]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AgentStep.step_index",
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_index", name="uq_agent_steps_run_id_step_index"),
    )

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
        UniqueConstraint(
            "thread_id", "sequence_id", name="uq_agent_messages_thread_id_sequence_id"
        ),
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
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    thread: Mapped[AgentThread] = relationship(back_populates="messages")


class MemoryItem(Base):
    __tablename__ = "memory_items"
    __table_args__ = (
        Index("ix_memory_items_user_category_active", "user_id", "category", "superseded_by"),
        Index("ix_memory_items_user_heat", "user_id", "heat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )  # fact, preference, goal, relationship, focus
    importance: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )  # 1-5
    source: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="extraction",
    )  # extraction, user, reflection
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("memory_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_referenced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reference_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    embedding_json: Mapped[list[float] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    tags_json: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    heat: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0.0"),
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

    tag_entries: Mapped[list[MemoryItemTag]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
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
        Integer,
        nullable=False,
        default=3,
    )  # 1-5
    turn_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message_indices_json: Mapped[list[int] | None] = mapped_column(
        JSON,
        nullable=True,
    )  # 1-based indices of included logs (batch segmentation)
    segmentation_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="sequential",
        server_default=text("'sequential'"),
    )  # "sequential" or "batch_llm"
    needs_regeneration: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SessionNote(Base):
    """Working memory: per-thread scratch notes the AI writes during a conversation.

    These are session-scoped — they persist within a thread but are not
    long-term memories. They can be promoted to MemoryItem if important enough.
    """

    __tablename__ = "session_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    note_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="observation",
    )  # observation, plan, context, emotion
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    promoted_to_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_items.id", ondelete="SET NULL"),
        nullable=True,
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


class MemoryItemTag(Base):
    """Junction table for tag-based memory filtering.

    Tags are stored both here (for efficient queries) and in
    MemoryItem.tags_json (for easy reads). Mirrors Letta's PassageTag pattern.
    """

    __tablename__ = "memory_item_tags"
    __table_args__ = (UniqueConstraint("item_id", "tag", name="uq_memory_item_tags_item_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tag: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class MemoryClaim(Base):
    """Canonical structured claim extracted from user memory.

    Replaces freeform text dedup with slot-based storage, confidence
    scores, and provenance tracking.
    """

    __tablename__ = "memory_claims"
    __table_args__ = (Index("ix_memory_claims_user_canonical", "user_id", "canonical_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="user",
    )  # user, other_person, entity
    namespace: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )  # fact, preference, goal, relationship
    slot: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )  # age, occupation, location, etc.
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[dict[str, object] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    polarity: Mapped[str] = mapped_column(
        String(12),
        nullable=False,
        default="positive",
    )  # positive, negative, neutral
    confidence: Mapped[float] = mapped_column(
        nullable=False,
        default=0.8,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
    )  # active, superseded, retracted
    canonical_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )  # e.g. "user:fact:occupation"
    source_kind: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="extraction",
    )  # extraction, user, reflection
    extractor: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="regex",
    )  # regex, llm, manual
    memory_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_claims.id", ondelete="SET NULL"),
        nullable=True,
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

    evidence: Mapped[list[MemoryClaimEvidence]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
    )


class MemoryClaimEvidence(Base):
    """Source evidence for a structured memory claim."""

    __tablename__ = "memory_claim_evidence"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("memory_claims.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
    )  # user_message, extraction, reflection
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    claim: Mapped[MemoryClaim] = relationship(back_populates="evidence")


class MemoryDailyLog(Base):
    __tablename__ = "memory_daily_logs"
    __table_args__ = (Index("ix_memory_daily_logs_user_date", "user_id", "date"),)

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


class MemoryVector(Base):
    __tablename__ = "memory_vectors"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False, default="fact")
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class ForgetAuditLog(Base):
    """Audit trail for intentional forgetting events.

    Records THAT forgetting occurred (timestamp, scope, trigger) without
    recording WHAT was forgotten, preserving the right to forget.
    """

    __tablename__ = "forget_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    forgotten_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    trigger: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )  # user_request, topic_forget, suppression
    scope: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )  # single, topic:{topic}, entity:{name}
    items_forgotten: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    derived_refs_affected: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )


class BackgroundTaskRun(Base):
    """Tracked background task execution for debugging and monitoring."""

    __tablename__ = "background_task_runs"
    __table_args__ = (Index("ix_bg_task_runs_user_status", "user_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
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
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class KGEntity(Base):
    """Knowledge graph entity: a person, place, organization, project, or concept."""

    __tablename__ = "kg_entities"
    __table_args__ = (
        UniqueConstraint("user_id", "name_normalized", name="uq_kg_entities_user_name"),
        Index("ix_kg_entities_user_type", "user_id", "entity_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_normalized: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'unknown'")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    mentions: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KGRelation(Base):
    """Knowledge graph relation: a typed edge between two entities."""

    __tablename__ = "kg_relations"
    __table_args__ = (
        Index("ix_kg_relations_source", "source_id"),
        Index("ix_kg_relations_dest", "destination_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[int] = mapped_column(
        ForeignKey("kg_entities.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(100), nullable=False)
    mentions: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    source_memory_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_items.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
