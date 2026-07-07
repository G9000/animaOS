"""Promote recurring emotional signals into enduring soul patterns."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anima_server.models.runtime_consciousness import CurrentEmotion
from anima_server.models.soul_consciousness import CoreEmotionalPattern

logger = logging.getLogger(__name__)

MIN_SIGNALS_FOR_PATTERN = 3
MIN_CONFIDENCE_FOR_PATTERN = 0.5

# Promotion gate: don't re-scan 50 signals and write SQLCipher rows on
# every soul-writer run (practically every turn).  Run when enough new
# signals accumulated, or when at least one arrived and enough time
# passed since the last promotion.
MIN_NEW_SIGNALS_FOR_PROMOTION = 3
PROMOTION_INTERVAL = timedelta(hours=1)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def should_promote_emotional_patterns(
    *,
    soul_db: Session,
    pg_db: Session,
    user_id: int,
) -> bool:
    """Restart-safe promotion gate derived from persisted state."""
    last_observed = _as_utc(
        soul_db.scalar(
            select(func.max(CoreEmotionalPattern.last_observed)).where(
                CoreEmotionalPattern.user_id == user_id
            )
        )
    )
    if last_observed is None:
        total = pg_db.scalar(
            select(func.count())
            .select_from(CurrentEmotion)
            .where(CurrentEmotion.user_id == user_id)
        )
        return (total or 0) >= MIN_SIGNALS_FOR_PATTERN

    new_signals = pg_db.scalar(
        select(func.count())
        .select_from(CurrentEmotion)
        .where(
            CurrentEmotion.user_id == user_id,
            CurrentEmotion.created_at > last_observed,
        )
    )
    new_count = new_signals or 0
    if new_count >= MIN_NEW_SIGNALS_FOR_PROMOTION:
        return True
    if new_count >= 1 and datetime.now(UTC) - last_observed >= PROMOTION_INTERVAL:
        return True
    return False


def promote_emotional_patterns(
    *,
    soul_db: Session,
    pg_db: Session,
    user_id: int,
) -> int:
    """Analyze recent emotional signals and promote recurring patterns to soul."""
    signals = pg_db.scalars(
        select(CurrentEmotion)
        .where(CurrentEmotion.user_id == user_id)
        .order_by(CurrentEmotion.created_at.desc())
        .limit(50)
    ).all()

    if len(signals) < MIN_SIGNALS_FOR_PATTERN:
        return 0

    emotion_counts: Counter[str] = Counter()
    emotion_evidence: dict[str, list[str]] = {}
    qualifying_signals: dict[str, list[CurrentEmotion]] = {}
    for signal in signals:
        if signal.confidence < MIN_CONFIDENCE_FOR_PATTERN:
            continue
        emotion_counts[signal.emotion] += 1
        qualifying_signals.setdefault(signal.emotion, []).append(signal)
        if signal.topic:
            emotion_evidence.setdefault(signal.emotion, []).append(signal.topic)

    promoted = 0
    now = datetime.now(UTC)

    for emotion, count in emotion_counts.items():
        if count < MIN_SIGNALS_FOR_PATTERN:
            continue

        existing = soul_db.scalar(
            select(CoreEmotionalPattern).where(
                CoreEmotionalPattern.user_id == user_id,
                CoreEmotionalPattern.dominant_emotion == emotion,
            )
        )

        topics = emotion_evidence.get(emotion, [])
        trigger = ", ".join(sorted(set(topics[:5]))) if topics else ""
        matching_signals = qualifying_signals.get(emotion, [])
        avg_confidence = sum(signal.confidence for signal in matching_signals) / count

        if existing is not None:
            existing.frequency = count
            existing.confidence = round(avg_confidence, 2)
            existing.last_observed = now
            if trigger:
                existing.trigger_context = trigger
        else:
            pattern_text = f"Tends toward {emotion}"
            if trigger:
                pattern_text += f" when discussing {trigger}"
            soul_db.add(
                CoreEmotionalPattern(
                    user_id=user_id,
                    pattern=pattern_text,
                    dominant_emotion=emotion,
                    trigger_context=trigger,
                    frequency=count,
                    confidence=round(avg_confidence, 2),
                    first_observed=now,
                    last_observed=now,
                )
            )
        promoted += 1

    soul_db.flush()
    return promoted
