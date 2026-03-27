"""Consciousness layer models: self-model blocks and emotional signals."""

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
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from anima_server.db.base import Base


class SelfModelBlock(Base):
    """Per-user self-model section.

    P3 keeps only soul-tier sections here: soul, persona, human, user_directive.
    Moved sections remain for backward compatibility during migration reads.
    """

    __tablename__ = "self_model_blocks"
    __table_args__ = (
        UniqueConstraint("user_id", "section", name="uq_self_model_blocks_user_section"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    section: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )  # soul, persona, human, user_directive, plus legacy compatibility sections
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="system",
    )  # system, sleep_time, post_turn, user_edit
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class AgentProfile(Base):
    """Structured agent identity attributes — fast-lookup companion to the soul self_model_block.

    Stores the discrete facts collected during the creation ceremony so they
    are queryable without parsing prose content. One row per user.
    """

    __tablename__ = "agent_profile"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    agent_name: Mapped[str] = mapped_column(String(50), nullable=False, default="Anima")
    creator_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    relationship: Mapped[str] = mapped_column(String(100), nullable=False, default="companion")
    setup_complete: Mapped[bool] = mapped_column(default=False, server_default="0")
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


class EmotionalSignal(Base):
    """Detected emotional signal from a conversation turn.

    Stores per-conversation emotion detections with confidence, trajectory,
    and evidence. Kept for backward compatibility; P3 writes new signals to
    runtime current_emotions instead.
    """

    __tablename__ = "emotional_signals"
    __table_args__ = (Index("ix_emotional_signals_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_threads.id", ondelete="SET NULL"),
        nullable=True,
    )
    emotion: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )  # frustrated, excited, anxious, calm, stressed, relieved, curious, disappointed
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    evidence_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="linguistic",
    )  # explicit, linguistic, behavioral, contextual
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trajectory: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="stable",
    )  # escalating, de-escalating, stable, shifted
    previous_emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    topic: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    acted_on: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
