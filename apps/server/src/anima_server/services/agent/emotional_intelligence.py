"""Emotional intelligence: detect, track, and synthesize user emotional signals."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import EmotionalSignal
from anima_server.models.runtime_consciousness import CurrentEmotion
from anima_server.services.data_crypto import df, ef

logger = logging.getLogger(__name__)

PRIMARY_EMOTIONS = frozenset(
    {
        "frustrated",
        "excited",
        "anxious",
        "calm",
        "stressed",
        "relieved",
        "curious",
        "disappointed",
    }
)

SECONDARY_EMOTIONS = frozenset(
    {"vulnerable", "proud", "overwhelmed", "playful"})

ATTACHMENT_EMOTIONS = frozenset(
    {
        "love",
        "longing",
        "desire",
        "tenderness",
        "jealousy",
        "protective",
        "affection",
        "infatuation",
        "devotion",
        "adoration",
        "missing",
        "yearning",
    }
)
ALL_EMOTIONS = PRIMARY_EMOTIONS | SECONDARY_EMOTIONS | ATTACHMENT_EMOTIONS


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
) -> CurrentEmotion | EmotionalSignal | None:
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

    if trajectory == "stable" and previous_emotion is None:
        prev = get_latest_signal(db, user_id=user_id)
        if prev is not None:
            previous_emotion = prev.emotion
            if prev.emotion != emotion:
                trajectory = "shifted"

    model = _emotion_model(db)
    signal = model(
        user_id=user_id,
        thread_id=thread_id,
        emotion=emotion,
        confidence=confidence,
        evidence_type=evidence_type,
        evidence=_stored_text(model, user_id=user_id,
                              field="evidence", value=evidence),
        trajectory=trajectory,
        previous_emotion=previous_emotion,
        topic=_stored_text(model, user_id=user_id, field="topic", value=topic),
    )
    db.add(signal)
    db.flush()

    _trim_signal_buffer(db, user_id=user_id)
    return signal


def get_latest_signal(
    db: Session,
    *,
    user_id: int,
) -> CurrentEmotion | EmotionalSignal | None:
    """Get the most recent emotional signal for a user."""
    model = _emotion_model(db)
    return db.scalar(
        select(model)
        .where(model.user_id == user_id)
        .order_by(model.created_at.desc())
        .limit(1)
    )


def get_recent_signals(
    db: Session,
    *,
    user_id: int,
    limit: int | None = None,
) -> list[CurrentEmotion | EmotionalSignal]:
    """Get recent emotional signals for a user."""
    max_signals = limit or settings.agent_emotional_signal_buffer_size
    model = _emotion_model(db)
    return list(
        db.scalars(
            select(model)
            .where(model.user_id == user_id)
            .order_by(model.created_at.desc())
            .limit(max_signals)
        ).all()
    )


def synthesize_emotional_context(
    db: Session,
    *,
    user_id: int,
) -> str:
    """Synthesize recent emotional signals into a context paragraph."""
    signals = get_recent_signals(db, user_id=user_id, limit=10)
    if not signals:
        return ""

    lines: list[str] = []
    total_len = 0
    budget = settings.agent_emotional_context_budget

    for signal in signals:
        conf_label = "strong" if signal.confidence >= 0.7 else "moderate"
        line = (
            f"- {signal.emotion} ({conf_label} signal"
            f"{', ' + signal.trajectory if signal.trajectory != 'stable' else ''})"
        )
        topic_text = _read_text(signal, user_id=user_id, field="topic")
        if topic_text:
            line += f" re: {topic_text}"
        evidence_text = _read_text(signal, user_id=user_id, field="evidence")
        if evidence_text and len(evidence_text) < 80:
            line += f" - {evidence_text}"

        if total_len + len(line) > budget:
            break
        lines.append(line)
        total_len += len(line)

    if not lines:
        return ""

    emotion_counts: dict[str, float] = {}
    for signal in signals[:5]:
        emotion_counts[signal.emotion] = emotion_counts.get(
            signal.emotion, 0) + signal.confidence

    dominant = max(
        emotion_counts, key=emotion_counts.get) if emotion_counts else "calm"
    recent_trajectory = signals[0].trajectory if len(
        signals) >= 2 else "stable"

    header = f"Dominant recent emotion: {dominant}"
    if recent_trajectory != "stable":
        header += f" ({recent_trajectory})"

    return header + "\n" + "\n".join(lines)


def _trim_signal_buffer(
    db: Session,
    *,
    user_id: int,
) -> None:
    """Remove oldest signals beyond the configured buffer size."""
    from sqlalchemy import delete as sa_delete
    from sqlalchemy import func as sa_func

    model = _emotion_model(db)
    max_size = settings.agent_emotional_signal_buffer_size
    total = (
        db.scalar(
            select(sa_func.count()).select_from(
                model).where(model.user_id == user_id)
        )
        or 0
    )
    if total <= max_size:
        return

    cutoff_id = db.scalar(
        select(model.id)
        .where(model.user_id == user_id)
        .order_by(model.created_at.desc())
        .offset(max_size)
        .limit(1)
    )
    if cutoff_id is not None:
        db.execute(
            sa_delete(model).where(
                model.user_id == user_id,
                model.id <= cutoff_id,
            )
        )
        db.flush()


def _emotion_model(db: Session):
    inspector = sa_inspect(db.connection())
    if inspector.has_table("current_emotions"):
        return CurrentEmotion
    return EmotionalSignal


def _stored_text(model: type[Any], *, user_id: int, field: str, value: str) -> str:
    if model is CurrentEmotion:
        return value
    return ef(user_id, value, table="emotional_signals", field=field)


def _read_text(signal: CurrentEmotion | EmotionalSignal, *, user_id: int, field: str) -> str:
    value = getattr(signal, field, "")
    if isinstance(signal, CurrentEmotion):
        return value or ""
    return df(user_id, value, table="emotional_signals", field=field)
