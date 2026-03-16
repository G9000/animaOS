"""Emotional intelligence: detect, track, and synthesize user emotional signals.

No other major agent memory system explicitly models user emotional state.
This module provides:
- Emotion detection from conversation turns (8 primary emotions)
- Rolling signal buffer per user
- Trajectory tracking (escalating, de-escalating, stable, shifted)
- Emotional context synthesis for the system prompt
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import EmotionalSignal
from anima_server.services.data_crypto import ef, df

logger = logging.getLogger(__name__)

PRIMARY_EMOTIONS = frozenset({
    "frustrated", "excited", "anxious", "calm",
    "stressed", "relieved", "curious", "disappointed",
})

SECONDARY_EMOTIONS = frozenset({
    "vulnerable", "proud", "overwhelmed", "playful",
})

ALL_EMOTIONS = PRIMARY_EMOTIONS | SECONDARY_EMOTIONS

EMOTION_EXTRACTION_PROMPT_FRAGMENT = """Also detect the user's emotional tone in this exchange:
- emotion: the primary emotion you detect (single word from: frustrated, excited, anxious, calm, stressed, relieved, curious, disappointed, vulnerable, proud, overwhelmed, playful)
- emotion_confidence: 0.0-1.0 how confident you are
- emotion_trajectory: escalating, de-escalating, stable, or shifted
- emotion_evidence_type: explicit, linguistic, behavioral, or contextual
- emotion_evidence: what specifically indicated this

Only report if confidence > 0.4.
If nothing notable, set emotion to null."""


def record_emotional_signal(
    db: Session,
    *,
    user_id: int,
    thread_id: int | None = None,
    emotion: str,
    confidence: float = 0.5,
    evidence_type: str = "linguistic",
    evidence: str = "",
    trajectory: str = "stable",
    previous_emotion: str | None = None,
    topic: str = "",
) -> EmotionalSignal | None:
    """Record an emotional signal if it passes confidence threshold."""
    if confidence < settings.agent_emotional_confidence_threshold:
        return None

    emotion = emotion.lower().strip()
    if emotion not in ALL_EMOTIONS:
        return None

    if evidence_type not in ("explicit", "linguistic", "behavioral", "contextual"):
        evidence_type = "linguistic"
    if trajectory not in ("escalating", "de-escalating", "stable", "shifted"):
        trajectory = "stable"

    # Determine trajectory from previous signal if not provided
    if trajectory == "stable" and previous_emotion is None:
        prev = get_latest_signal(db, user_id=user_id)
        if prev is not None:
            previous_emotion = prev.emotion
            if prev.emotion != emotion:
                trajectory = "shifted"

    signal = EmotionalSignal(
        user_id=user_id,
        thread_id=thread_id,
        emotion=emotion,
        confidence=confidence,
        evidence_type=evidence_type,
        evidence=ef(user_id, evidence),
        trajectory=trajectory,
        previous_emotion=previous_emotion,
        topic=ef(user_id, topic),
    )
    db.add(signal)
    db.flush()

    # Enforce buffer size — remove oldest signals beyond limit
    _trim_signal_buffer(db, user_id=user_id)

    return signal


def get_latest_signal(
    db: Session,
    *,
    user_id: int,
) -> EmotionalSignal | None:
    """Get the most recent emotional signal for a user."""
    return db.scalar(
        select(EmotionalSignal)
        .where(EmotionalSignal.user_id == user_id)
        .order_by(EmotionalSignal.created_at.desc())
        .limit(1)
    )


def get_recent_signals(
    db: Session,
    *,
    user_id: int,
    limit: int | None = None,
) -> list[EmotionalSignal]:
    """Get recent emotional signals for a user."""
    max_signals = limit or settings.agent_emotional_signal_buffer_size
    return list(
        db.scalars(
            select(EmotionalSignal)
            .where(EmotionalSignal.user_id == user_id)
            .order_by(EmotionalSignal.created_at.desc())
            .limit(max_signals)
        ).all()
    )


def synthesize_emotional_context(
    db: Session,
    *,
    user_id: int,
) -> str:
    """Synthesize recent emotional signals into a context paragraph for the prompt.

    Returns a brief "gut feeling" paragraph about how the user seems, or empty
    string if insufficient data.
    """
    signals = get_recent_signals(db, user_id=user_id, limit=10)
    if not signals:
        return ""

    # Build a summary from recent signals
    lines: list[str] = []
    total_len = 0
    budget = settings.agent_emotional_context_budget

    # Group by recency — most recent first
    for signal in signals:
        conf_label = "strong" if signal.confidence >= 0.7 else "moderate"
        line = (
            f"- {signal.emotion} ({conf_label} signal"
            f"{', ' + signal.trajectory if signal.trajectory != 'stable' else ''}"
            f")"
        )
        topic_text = df(user_id, signal.topic)
        if topic_text:
            line += f" re: {topic_text}"
        evidence_text = df(user_id, signal.evidence)
        if evidence_text and len(evidence_text) < 80:
            line += f" — {evidence_text}"

        if total_len + len(line) > budget:
            break
        lines.append(line)
        total_len += len(line)

    if not lines:
        return ""

    # Determine dominant emotion
    emotion_counts: dict[str, float] = {}
    for s in signals[:5]:
        emotion_counts[s.emotion] = emotion_counts.get(
            s.emotion, 0) + s.confidence

    dominant = max(
        emotion_counts, key=emotion_counts.get) if emotion_counts else "calm"

    # Check trajectory
    if len(signals) >= 2:
        recent_trajectory = signals[0].trajectory
    else:
        recent_trajectory = "stable"

    header = f"Dominant recent emotion: {dominant}"
    if recent_trajectory != "stable":
        header += f" ({recent_trajectory})"

    return header + "\n" + "\n".join(lines)


def _trim_signal_buffer(
    db: Session,
    *,
    user_id: int,
) -> None:
    """Remove oldest signals beyond the buffer size limit."""
    max_size = settings.agent_emotional_signal_buffer_size
    signals = list(
        db.scalars(
            select(EmotionalSignal)
            .where(EmotionalSignal.user_id == user_id)
            .order_by(EmotionalSignal.created_at.desc())
        ).all()
    )
    if len(signals) <= max_size:
        return
    for old_signal in signals[max_size:]:
        db.delete(old_signal)
    db.flush()
