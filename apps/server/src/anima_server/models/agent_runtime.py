from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
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
        Index("ix_memory_items_user_decay_class", "user_id", "decay_class"),
        Index("ix_memory_items_user_evolves_from", "user_id", "evolves_from_item_id"),
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
    embedding_checksum: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    tags_json: Mapped[list[str] | None] = mapped_column(
        JSON,
        nullable=True,
    )
    memory_class: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="casual",
        server_default=text("'casual'"),
    )
    emotional_salience: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0.0"),
    )
    stability_class: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="stable",
        server_default=text("'stable'"),
    )
    decay_class: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="standard",
        server_default=text("'standard'"),
    )
    relationship_proximity: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0.0"),
    )
    evidence_strength: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.8,
        server_default=text("0.8"),
    )
    evolves_from_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    evolution_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    # IL5 forgetting-as-distillation tombstone marker: set when passive decay
    # folded this item's signature into a ``tendency`` claim and gutted its
    # content/embedding/evidence in place (id, memory_class, category, and
    # created_at survive). NULL means "never distilled" — retrieval paths
    # that don't already heat-gate must additionally require this NULL (see
    # ``services/agent/distillation.py``).
    distilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # IL6 recall-reconsolidation: cumulative absolute emotional_salience
    # drift applied by reconsolidation across this item's whole life,
    # bounded by ``reconsolidation_lifetime_drift_cap`` (default 0.3).
    # Tracked separately from ``emotional_salience`` itself so the cap is
    # exactly enforceable regardless of how the field's absolute value
    # wanders from other write paths (merge_salience, decay, etc.) — see
    # ``services/agent/reconsolidation.py``.
    reconsolidation_drift: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0.0"),
    )

    tag_entries: Mapped[list[MemoryItemTag]] = relationship(
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    evidence: Mapped[list[MemoryItemEvidence]] = relationship(
        back_populates="memory_item",
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
    transcript_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
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


class ForesightSignal(Base):
    """Evidence-backed future-oriented memory signal."""

    __tablename__ = "foresight_signals"
    __table_args__ = (
        Index("ix_foresight_signals_user_status", "user_id", "status"),
        Index("ix_foresight_signals_user_start", "user_id", "start_date"),
        Index("ix_foresight_signals_user_thread", "user_id", "source_thread_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    relative_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.8,
        server_default=text("0.8"),
    )
    source_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_message_ids_json: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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


class AgentExperience(Base):
    """Durable procedural memory of the agent's own problem solving."""

    __tablename__ = "agent_experiences"
    __table_args__ = (
        Index("ix_agent_experiences_user", "user_id"),
        Index("ix_agent_experiences_user_cluster", "user_id", "cluster_id"),
        Index("ix_agent_experiences_user_active", "user_id", "superseded_by"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_intent: Mapped[str] = mapped_column(Text, nullable=False)
    approach: Mapped[str] = mapped_column(Text, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    source_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_names_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    cluster_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("agent_experiences.id", ondelete="SET NULL"),
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


class ExperienceClusterState(Base):
    """Serialized incremental centroid state for procedural experiences."""

    __tablename__ = "experience_cluster_state"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_experience_cluster_state_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    state_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
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


class AgentSkill(Base):
    """Distilled reusable procedure derived from clustered agent experiences."""

    __tablename__ = "agent_skills"
    __table_args__ = (
        Index("ix_agent_skills_user_cluster", "user_id", "cluster_id"),
        Index("ix_agent_skills_user_active", "user_id", "superseded_by"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    cluster_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    experience_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_refined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    superseded_by: Mapped[int | None] = mapped_column(
        ForeignKey("agent_skills.id", ondelete="SET NULL"),
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


class DiaryFolder(Base):
    """User-defined grouping ("notebook") for diary entries."""

    __tablename__ = "diary_folders"
    __table_args__ = (
        Index("ix_diary_folders_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DiaryEntry(Base):
    """User-authored private daily diary/log entry."""

    __tablename__ = "diary_entries"
    __table_args__ = (
        Index("ix_diary_entries_user_date", "user_id", "entry_date"),
        Index("ix_diary_entries_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    entry_date: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    mood: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="user",
        server_default=text("'user'"),
    )
    cover_attachment_id: Mapped[int | None] = mapped_column(
        ForeignKey("diary_attachments.id", ondelete="SET NULL"),
        nullable=True,
    )
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("diary_folders.id", ondelete="SET NULL"),
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

    attachments: Mapped[list[DiaryAttachment]] = relationship(
        back_populates="entry",
        foreign_keys="[DiaryAttachment.entry_id]",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DiaryAttachment.created_at",
    )


class DiaryAttachment(Base):
    """Encrypted local blob attached to a diary entry."""

    __tablename__ = "diary_attachments"
    __table_args__ = (
        Index("ix_diary_attachments_user_entry", "user_id", "entry_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("diary_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    entry: Mapped[DiaryEntry] = relationship(back_populates="attachments", foreign_keys=[entry_id])


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


class MemoryItemEvidence(Base):
    """Source evidence for a durable memory item."""

    __tablename__ = "memory_item_evidence"
    __table_args__ = (
        Index("ix_memory_item_evidence_user_item", "user_id", "memory_item_id"),
        Index("ix_memory_item_evidence_user_observed", "user_id", "observed_at"),
        Index(
            "ix_memory_item_evidence_source_observed",
            "user_id",
            "source_kind",
            "observed_at",
        ),
        Index("ix_memory_item_evidence_runtime_message", "runtime_message_id"),
        Index("ix_memory_item_evidence_transcript_ref", "transcript_ref"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_item_id: Mapped[int] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    runtime_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_message_ids_json: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    transcript_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sequence_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    speaker: Mapped[str | None] = mapped_column(String(24), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    source_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        server_default=text("1.0"),
    )
    extractor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    memory_item: Mapped[MemoryItem] = relationship(back_populates="evidence")


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


class UserProfileField(Base):
    """Evidence-backed structured user profile field.

    Active rows describe Anima's current compact model of the user; older
    rows remain for audit when a field is corrected or superseded.
    """

    __tablename__ = "user_profile_fields"
    __table_args__ = (
        Index("ix_user_profile_fields_user_status", "user_id", "status"),
        Index(
            "ix_user_profile_fields_user_category_key",
            "user_id",
            "category",
            "key",
        ),
        Index("ix_user_profile_fields_superseded_by", "superseded_by_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.8,
        server_default=text("0.8"),
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    source_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="extraction",
        server_default=text("'extraction'"),
    )
    source_memory_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_item_evidence.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_claim_evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_claim_evidence.id", ondelete="SET NULL"),
        nullable=True,
    )
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_profile_fields.id", ondelete="SET NULL"),
        nullable=True,
    )
    first_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
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

    evidence: Mapped[list[UserProfileFieldEvidence]] = relationship(
        back_populates="profile_field",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="UserProfileFieldEvidence.profile_field_id",
    )


class UserProfileFieldEvidence(Base):
    """Evidence row supporting a structured user profile field."""

    __tablename__ = "user_profile_field_evidence"
    __table_args__ = (
        Index("ix_user_profile_field_evidence_user_field", "user_id", "profile_field_id"),
        Index("ix_user_profile_field_evidence_user_observed", "user_id", "observed_at"),
        Index("ix_user_profile_field_evidence_memory", "source_memory_id"),
        Index("ix_user_profile_field_evidence_source_evidence", "source_evidence_id"),
        Index(
            "ix_user_profile_field_evidence_source_claim_evidence",
            "source_claim_evidence_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    profile_field_id: Mapped[int] = mapped_column(
        ForeignKey("user_profile_fields.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_memory_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_item_evidence.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_claim_evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_claim_evidence.id", ondelete="SET NULL"),
        nullable=True,
    )
    runtime_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    profile_field: Mapped[UserProfileField] = relationship(
        back_populates="evidence",
        foreign_keys=[profile_field_id],
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
    aliases_json: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    embedding_json: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    embedding_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
        Index("ix_kg_relations_user_status", "user_id", "status"),
        Index("ix_kg_relations_user_source_type", "user_id", "source_id", "relation_type"),
        Index("ix_kg_relations_user_observed", "user_id", "observed_at"),
        Index("ix_kg_relations_evidence", "evidence_id"),
        Index("ix_kg_relations_supersedes", "supersedes_relation_id"),
        Index("ix_kg_relations_evolves_from", "evolves_from_relation_id"),
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
    evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("memory_item_evidence.id", ondelete="SET NULL"), nullable=True
    )
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default=text("1.0")
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="active", server_default=text("'active'")
    )
    supersedes_relation_id: Mapped[int | None] = mapped_column(
        ForeignKey("kg_relations.id", ondelete="SET NULL"), nullable=True
    )
    evolves_from_relation_id: Mapped[int | None] = mapped_column(
        ForeignKey("kg_relations.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LatentTrace(Base):
    """IL4 sub-threshold memory accumulator: a weighted latent trace per topic.

    Soul-store (SQLCipher), portable, included in vault export/import (PRD
    §5 — right-to-forget bullets bring it inside the F7 deletion boundary).
    ``topic_key`` mirrors ``MemoryClaim.canonical_key``: a derived structural
    identifier (see ``claims.derive_topic_key``), never raw user content, so
    — like ``canonical_key`` — it is stored in plaintext for indexed
    equality lookups rather than field-encrypted; there is no other
    content-bearing column on this table (``evidence_refs`` holds only
    identifiers — candidate id, source message ids, content hash — never
    copied text, following the house convention that ``_json`` columns are
    structural/metadata, not encrypted content; see
    ``MemoryItemEvidence.metadata_json`` for the same pattern).
    """

    __tablename__ = "latent_traces"
    __table_args__ = (
        UniqueConstraint("user_id", "topic_key", name="uq_latent_traces_user_topic"),
        Index("ix_latent_traces_user_weight", "user_id", "weight"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    topic_key: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="observation", server_default=text("'observation'")
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0.0"))
    evidence_refs: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TendencyContribution(Base):
    """IL5 forgetting-as-distillation ledger row.

    Records one distilled (tombstoned) ``MemoryItem``'s numeric contribution
    toward a ``tendency``-namespace ``MemoryClaim``. Soul-store, portable,
    included in vault export/import (PRD §5 — the ledger and its tombstones
    cannot be rebuilt once their source content is deleted, so right-to-forget
    for already-distilled items depends on this table surviving rebuilds and
    export/import).

    Both foreign keys are soul-store-local (``memory_items``,
    ``memory_claims``) — unlike ``LatentTrace.evidence_refs``, which can
    reference runtime-store ids that collide across a vault import, there is
    no cross-store id-collision risk here.

    ``contribution_vector`` holds numeric salience deltas only, e.g.
    ``{"strength": s, "valence_hint": v}`` — never content. A tendency
    claim's aggregate strength is always recomputed from surviving rows here
    by ``services.agent.distillation.recompute_tendency_from_ledger``, the
    single source of truth both the distill path and the right-to-forget
    path use, so a single contribution can be removed exactly later.
    """

    __tablename__ = "tendency_contributions"
    __table_args__ = (
        # One tombstone distills into exactly one tendency — makes
        # concurrent sleep pipelines safe (loser's insert fails, per-item
        # transaction rolls back) instead of double-counting.
        UniqueConstraint(
            "tombstone_item_id",
            name="uq_tendency_contributions_tombstone_item_id",
        ),
        Index("ix_tendency_contributions_user_id", "user_id"),
        Index("ix_tendency_contributions_claim", "tendency_claim_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tombstone_item_id: Mapped[int] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    tendency_claim_id: Mapped[int] = mapped_column(
        ForeignKey("memory_claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    contribution_vector: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class ReconsolidationLog(Base):
    """IL6 recall-reconsolidation provenance ledger row.

    Every applied per-field nudge (post drift-cap) writes exactly one row
    here preserving the ORIGINAL pre-nudge value in ``old_value`` — a
    no-op (cap already exhausted, or a zero delta) writes nothing.
    Reversibility is exact by construction: reconstructing the
    pre-reconsolidation salience never replays/sums deltas (which would
    accumulate floating-point error across N applications) — it just reads
    the OLDEST logged ``old_value`` per field, which IS the original
    extracted value (see
    ``services.agent.reconsolidation.original_salience_from_log``).

    Numeric only, no content (soul-store, portable, included in vault
    export/import — mirrors ``TendencyContribution``). ``field`` is one of
    ``"emotional_salience"`` (raw [0,1] value) or ``"stability_class"``
    (the ``_STABILITY_STRENGTH`` rank, not the string, to keep this table
    numeric-only).
    """

    __tablename__ = "reconsolidation_log"
    __table_args__ = (
        Index("ix_reconsolidation_log_user_id", "user_id"),
        Index("ix_reconsolidation_log_item", "memory_item_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_item_id: Mapped[int] = mapped_column(
        ForeignKey("memory_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    old_value: Mapped[float] = mapped_column(Float, nullable=False)
    new_value: Mapped[float] = mapped_column(Float, nullable=False)
    eta: Mapped[float] = mapped_column(Float, nullable=False)


class InitiativeLog(Base):
    """IL3 push-initiative provenance row: the answer to "why did it message me?"

    Written exactly once per gate-chain pass that clears every gate and has a
    dominant drive (``services.agent.inner_life.initiative.tick_initiative_for_user``),
    whether or not message generation actually succeeded — a failed
    generation still writes a row here with ``generated_text=None`` and
    ``delivered=False`` (a logged best-effort attempt, per PRD IL3: "on
    generation failure ... log the attempt"), so every gate-chain pass that
    reached the fire step is inspectable, not just the successful ones.
    ``pressure_snapshot``/``gate_states`` are numeric/boolean JSON only (all
    five drive pressures and every named gate at fire time) — no external
    content beyond the one generated message itself. ``generated_text`` is
    field-encrypted like other free-text soul columns (see
    ``services.data_crypto`` domain map); pressure/gate JSON needs no such
    encryption (numeric/boolean only, mirrors ``TendencyContribution`` /
    ``ReconsolidationLog``). ``answered`` flips true via the pending-initiative
    ack API route once the user acknowledges the delivered message — it is
    what feeds the gate chain's unanswered-initiative cooldown backoff.
    """

    __tablename__ = "initiative_log"
    __table_args__ = (
        Index("ix_initiative_log_user_fired", "user_id", "fired_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    drive: Mapped[str] = mapped_column(String(32), nullable=False)
    pressure_snapshot: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    gate_states: Mapped[dict[str, bool]] = mapped_column(JSON, nullable=False)
    generated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    answered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DreamJournal(Base):
    """IL7 dream-cycle entry: one recombination of important-but-cold material
    into a short narrative during an idle night window.

    Soul-store (portable, encrypted, vault-exported) — unlike IL1/IL3 runtime
    state, a dream is durable autobiographical content the companion may later
    surface. ``narrative`` is field-encrypted like other free-text soul columns
    (see ``services.data_crypto`` domain map); ``source_refs`` and
    ``affect_delta`` are numeric/structural JSON only (the memory-item ids /
    latent-trace ids / transcript ref that seeded the dream, and the applied
    valence/arousal/energy deltas) so provenance is inspectable without leaking
    content. ``share_worthy`` records whether the dream drew on high-significance
    material and therefore raised IL3 ``dream_residue``. The table is capped at
    a rolling 30 rows per user in the write path (no schema-level cap)."""

    __tablename__ = "dream_journal"
    __table_args__ = (
        Index("ix_dream_journal_user_dreamt", "user_id", "dreamt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    dreamt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    source_refs: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    affect_delta: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    share_worthy: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    surfaced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
