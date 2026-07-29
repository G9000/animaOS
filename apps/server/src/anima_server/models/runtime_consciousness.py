"""Runtime-tier consciousness models (PostgreSQL).

Working cognition that is ephemeral - discarded on machine transfer,
rebuilt from seed values on next startup.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON as SA_JSON,
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


class AffectStateRow(RuntimeBase):
    """Persisted affect state vector (IL1): valence/arousal/energy dynamics."""

    __tablename__ = "affect_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    valence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    arousal: Mapped[float] = mapped_column(Float, nullable=False, default=0.35)
    energy: Mapped[float] = mapped_column(Float, nullable=False, default=0.6)
    arousal_baseline_shift: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    high_arousal_hours: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PresenceCatchup(RuntimeBase):
    """Audit row for one offline catch-up application (IL2).

    Written once per user per catch-up (startup after a gap); rows
    accumulate across restarts for inspectability, so ``user_id`` is
    indexed but not unique.
    """

    __tablename__ = "presence_catchup"
    __table_args__ = (
        Index("ix_presence_catchup_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gap_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    components: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    dream_deferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )


class DriveStateRow(RuntimeBase):
    """Persisted IL3 drive-pressure state (rebuildable — see
    ``services.agent.inner_life.drives`` for the pure accumulator math this
    backs). One row per user.

    ``updated_at`` is the Ī”t reference the presence tick uses to compute
    elapsed hours since the last advance (mirrors ``AffectStateRow``) — it is
    bookkeeping the PRD's drive table doesn't name explicitly, same as
    ``AffectStateRow.updated_at`` for IL1.

    ``pattern_insight_surfaced_at`` is similar bookkeeping specific to the
    ``pattern_insight`` drive: unlike ``unresolved_thread`` (which resets
    structurally whenever its ForesightSignal is no longer open) or
    ``relational``/``novelty`` (reset by an observable turn/topic event),
    "not yet shared" for a pattern-synthesis finding has no natural
    structural marker — pattern MemoryItem rows persist forever once
    created. This column is the "has this finding already been surfaced"
    marker the growth signal needs; it only advances when an initiative
    actually fires on ``pattern_insight`` (``reset_drive`` at the edge).
    ``pattern_insight_surfaced_id`` is a same-timestamp tie-breaker: two
    unsurfaced pattern rows can share an identical ``created_at`` (same-second
    bulk insert, or a vault restore), and ``created_at`` alone can't
    distinguish "the one just surfaced" from "its still-unvoiced sibling at
    the same instant" — a strict ``created_at > marker`` would silently drop
    both. The pair ``(pattern_insight_surfaced_at, pattern_insight_surfaced_id)``
    is compared lexicographically (see
    ``services.agent.inner_life.initiative._unsurfaced_pattern_query``), so a
    same-timestamp sibling with a higher id still counts as unsurfaced.

    ``last_fired_at``/``unanswered_initiatives`` feed the gate chain's
    adaptive cooldown (``services.agent.inner_life.initiative.should_fire``);
    ``last_user_turn_at`` is what lets the tick detect a NEW user turn since
    the previous tick (a raw ``RuntimeThread.last_message_at`` read can't
    distinguish "the same old message" from "a new one" on its own).
    """

    __tablename__ = "drive_states"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    unresolved_thread: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pattern_insight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    relational: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    novelty: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    dream_residue: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_fired_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    last_user_turn_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    pattern_insight_surfaced_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ, nullable=True
    )
    pattern_insight_surfaced_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    # IL7: last time a dream was ATTEMPTED (reached the extraction call),
    # success or failure — bounds the dream to <=1 extraction call per night
    # even when generation persistently fails (a failed dream writes no
    # dream_journal row, so this is what the nightly cap counts).
    last_dream_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMPTZ, nullable=True
    )
    unanswered_initiatives: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # IL-013: per-drive count of initiative selections this drive lost while
    # above its theta ({drive_name: losses}); feeds the bounded ranking boost
    # in ``initiative.dominant_drive`` so a chronically outranked drive
    # eventually surfaces. NULL/missing keys mean zero losses. Reset per
    # drive when it fires or when its pressure is hard-reset.
    starvation_losses: Mapped[dict | None] = mapped_column(SA_JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PendingInitiative(RuntimeBase):
    """A fired IL3 initiative awaiting client pickup (the default,
    always-available delivery adapter — see
    ``services.agent.inner_life.delivery.PendingInitiativeDelivery``).

    ``initiative_log_id`` references ``InitiativeLog.id`` in the SOUL store
    by plain integer, not a SQL foreign key — the two tables live in
    different physical databases (runtime Postgres/SQLite vs. SQLCipher
    soul store), the same cross-tier reference style already used by
    ``MemoryRetrievalFeedback.memory_item_id``. ``delivered`` flips true once
    a client GET has returned the row; ``acknowledged`` flips true via the
    ack API route, which is also what marks the soul-store ``InitiativeLog``
    row ``answered`` for the cooldown backoff.
    """

    __tablename__ = "pending_initiatives"
    __table_args__ = (
        Index("ix_pending_initiatives_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    initiative_log_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    drive: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ,
        nullable=False,
        server_default=func.now(),
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
