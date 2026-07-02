"""Cross-episode pattern synthesis.

Sleep-time task that samples episodes across time/topic/salience, asks the
LLM for recurring patterns, and stores only repeated evidence as durable
pattern memories.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import MemoryEpisode, MemoryItem
from anima_server.services.agent.llm_json import call_llm_for_json
from anima_server.services.agent.memory_salience import (
    DECAY_SLOW,
    MEMORY_CLASS_ACTIVE_PROJECT,
    MEMORY_CLASS_EMOTIONAL_PATTERN,
    MEMORY_CLASS_RELATIONSHIP,
    STABILITY_EVOLVING,
    STABILITY_STABLE,
)
from anima_server.services.agent.memory_store import store_memory_item
from anima_server.services.agent.provenance import add_memory_item_evidence
from anima_server.services.data_crypto import df

PATTERN_CATEGORY = "pattern"
PATTERN_SOURCE = "pattern_synthesis"
MIN_PATTERN_CONFIDENCE = 0.65
MIN_SOURCE_EPISODES = 2
DEFAULT_EPISODE_SAMPLE_LIMIT = 24
DEFAULT_PATTERN_LIMIT = 8

_ALLOWED_OUTPUT_CATEGORIES = frozenset(
    {
        "emotional_patterns",
        "goals",
        "preferences",
        "relationships",
        "work",
        "values",
        "constraints",
        "active_projects",
        "identity",
    }
)


@dataclass(frozen=True, slots=True)
class PatternCandidate:
    pattern: str
    category: str
    confidence: float
    source_episode_ids: tuple[int, ...]
    evidence: tuple[str, ...]
    source_evidence_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class PatternSynthesisResult:
    sampled: int = 0
    proposed: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0


def sample_pattern_episodes(
    db: Session,
    *,
    user_id: int,
    limit: int = DEFAULT_EPISODE_SAMPLE_LIMIT,
) -> tuple[MemoryEpisode, ...]:
    """Sample episodes across temporal windows, topics, and salience."""
    if limit <= 0:
        return ()

    episodes = list(
        db.scalars(
            select(MemoryEpisode)
            .where(MemoryEpisode.user_id == user_id)
            .order_by(MemoryEpisode.created_at.desc(), MemoryEpisode.id.desc())
            .limit(max(limit * 8, 64))
        ).all()
    )
    if len(episodes) <= limit:
        return tuple(_sort_sample_for_prompt(episodes))

    selected: list[MemoryEpisode] = []
    seen: set[int] = set()

    def add(candidates: Iterable[MemoryEpisode], *, cap: int | None = None) -> None:
        added = 0
        for episode in candidates:
            if episode.id in seen:
                continue
            selected.append(episode)
            seen.add(int(episode.id))
            added += 1
            if len(selected) >= limit:
                return
            if cap is not None and added >= cap:
                return

    salience_cap = max(1, limit // 3)
    add(sorted(episodes, key=_episode_salience_key, reverse=True), cap=salience_cap)

    for bucket in _temporal_buckets(episodes).values():
        add(sorted(bucket, key=_episode_salience_key, reverse=True), cap=1)
        if len(selected) >= limit:
            return tuple(_sort_sample_for_prompt(selected))

    topic_best: dict[str, MemoryEpisode] = {}
    for episode in episodes:
        for topic in _episode_topics(episode):
            current = topic_best.get(topic)
            if current is None or _episode_salience_key(episode) > _episode_salience_key(current):
                topic_best[topic] = episode
    add(sorted(topic_best.values(), key=_episode_salience_key, reverse=True))

    if len(selected) < limit:
        add(sorted(episodes, key=_episode_salience_key, reverse=True))

    return tuple(_sort_sample_for_prompt(selected[:limit]))


def parse_pattern_response(value: object) -> tuple[PatternCandidate, ...]:
    """Parse strict JSON pattern output and discard weak singletons."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if not isinstance(value, list):
        return ()

    parsed: list[PatternCandidate] = []
    seen: set[str] = set()
    for raw in value:
        candidate = _parse_candidate(raw)
        if candidate is None:
            continue
        dedupe_key = candidate.pattern.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        parsed.append(candidate)
    return tuple(parsed)


async def synthesize_cross_episode_patterns(
    *,
    user_id: int,
    db_factory: Callable[..., object] | None = None,
    episode_limit: int = DEFAULT_EPISODE_SAMPLE_LIMIT,
    pattern_limit: int = DEFAULT_PATTERN_LIMIT,
) -> PatternSynthesisResult:
    """Create durable pattern memories from repeated cross-episode evidence."""
    if settings.agent_provider == "scaffold":
        return PatternSynthesisResult()

    from anima_server.db.session import SessionLocal
    from anima_server.services.agent.prompt_loader import PromptLoader

    factory = db_factory or SessionLocal
    with factory() as db:
        episodes = sample_pattern_episodes(db, user_id=user_id, limit=episode_limit)
        if len(episodes) < MIN_SOURCE_EPISODES:
            return PatternSynthesisResult(sampled=len(episodes))

        prompt = PromptLoader.from_db(db, user_id).pattern_synthesis(
            episodes=_render_episodes_for_prompt(episodes, user_id=user_id),
        )
        response = await call_llm_for_json(
            "You synthesize user memory patterns. Respond only with strict JSON.",
            prompt,
            expect="array",
        )
        patterns = parse_pattern_response(response)
        if pattern_limit > 0:
            patterns = patterns[:pattern_limit]

        allowed_episode_ids = {int(episode.id) for episode in episodes}
        created = 0
        updated = 0
        skipped = 0
        for pattern in patterns:
            if not set(pattern.source_episode_ids).issubset(allowed_episode_ids):
                skipped += 1
                continue
            item, action = _store_pattern(db, user_id=user_id, pattern=pattern)
            if item is None:
                skipped += 1
                continue
            if action == "duplicate":
                updated += 1
            elif action == "added":
                created += 1
            else:
                skipped += 1

        if created or updated:
            db.commit()

    return PatternSynthesisResult(
        sampled=len(episodes),
        proposed=len(patterns),
        created=created,
        updated=updated,
        skipped=skipped,
    )


def _store_pattern(
    db: Session,
    *,
    user_id: int,
    pattern: PatternCandidate,
) -> tuple[MemoryItem | None, str]:
    result = store_memory_item(
        db,
        user_id=user_id,
        content=pattern.pattern,
        category=PATTERN_CATEGORY,
        importance=_importance_for_confidence(pattern.confidence),
        source=PATTERN_SOURCE,
        allow_update=False,
        tags=["pattern", pattern.category],
        salience=_salience_for_pattern(pattern),
    )
    item = result.item or result.matched_item
    if item is None or result.action in {"conflict", "rejected", "similar"}:
        return None, result.action

    metadata: dict[str, object] = {
        "memory_source": PATTERN_SOURCE,
        "source_episode_ids": list(pattern.source_episode_ids),
    }
    if pattern.source_evidence_ids:
        metadata["source_evidence_ids"] = list(pattern.source_evidence_ids)

    add_memory_item_evidence(
        db,
        user_id=user_id,
        memory_item_id=item.id,
        evidence_text=_evidence_text(pattern),
        source_kind=PATTERN_SOURCE,
        speaker="system",
        observed_at=_latest_episode_observed_at(
            db,
            user_id=user_id,
            episode_ids=pattern.source_episode_ids,
        ),
        confidence=pattern.confidence,
        extractor=PATTERN_SOURCE,
        metadata=metadata,
    )
    return item, result.action


def _parse_candidate(raw: object) -> PatternCandidate | None:
    if not isinstance(raw, dict):
        return None
    pattern = _clean_text(raw.get("pattern") or raw.get("content"), limit=280)
    if not pattern:
        return None
    confidence = _bounded_float(raw.get("confidence"), default=0.0)
    if confidence < MIN_PATTERN_CONFIDENCE:
        return None
    source_episode_ids = _int_tuple(raw.get("source_episode_ids") or raw.get("episode_ids"))
    if len(source_episode_ids) < MIN_SOURCE_EPISODES:
        return None
    evidence = tuple(
        text
        for text in (_clean_text(item, limit=240) for item in _as_list(raw.get("evidence")))
        if text
    )
    if len(evidence) < MIN_SOURCE_EPISODES:
        return None
    category = _normalize_output_category(raw.get("category"))
    source_evidence_ids = _int_tuple(raw.get("source_evidence_ids") or raw.get("evidence_ids"))
    return PatternCandidate(
        pattern=pattern,
        category=category,
        confidence=confidence,
        source_episode_ids=source_episode_ids,
        evidence=evidence,
        source_evidence_ids=source_evidence_ids,
    )


def _render_episodes_for_prompt(
    episodes: Sequence[MemoryEpisode],
    *,
    user_id: int,
) -> str:
    lines: list[str] = []
    for episode in episodes:
        summary = df(user_id, episode.summary, table="memory_episodes", field="summary")
        topics = ", ".join(_episode_topics(episode)) or "none"
        emotional_arc = episode.emotional_arc or "unknown"
        lines.append(
            "\n".join(
                [
                    f"Episode {episode.id}",
                    f"- date: {episode.date}",
                    f"- topics: {topics}",
                    f"- salience: {episode.significance_score}",
                    f"- emotional_arc: {emotional_arc}",
                    f"- summary: {summary}",
                ]
            )
        )
    return "\n\n".join(lines)


def _episode_topics(episode: MemoryEpisode) -> tuple[str, ...]:
    raw_topics = episode.topics_json or []
    return tuple(
        normalized
        for normalized in (
            str(topic).strip().casefold() for topic in raw_topics if topic is not None
        )
        if normalized
    )


def _episode_salience_key(episode: MemoryEpisode) -> tuple[int, datetime, int]:
    return (
        int(episode.significance_score or 0),
        _episode_datetime(episode),
        int(episode.id or 0),
    )


def _sort_sample_for_prompt(episodes: Sequence[MemoryEpisode]) -> list[MemoryEpisode]:
    return sorted(episodes, key=lambda episode: (_episode_datetime(episode), int(episode.id or 0)))


def _temporal_buckets(episodes: Sequence[MemoryEpisode]) -> dict[str, list[MemoryEpisode]]:
    newest = max((_episode_datetime(episode) for episode in episodes), default=datetime.now(UTC))
    buckets = {"recent": [], "mid": [], "older": []}
    for episode in episodes:
        age_days = (newest - _episode_datetime(episode)).days
        if age_days <= 30:
            buckets["recent"].append(episode)
        elif age_days <= 180:
            buckets["mid"].append(episode)
        else:
            buckets["older"].append(episode)
    return buckets


def _episode_datetime(episode: MemoryEpisode) -> datetime:
    created = episode.created_at
    if created is not None:
        if created.tzinfo is None:
            return created.replace(tzinfo=UTC)
        return created
    try:
        return datetime.fromisoformat(f"{episode.date}T00:00:00+00:00")
    except ValueError:
        return datetime.now(UTC)


def _salience_for_pattern(pattern: PatternCandidate) -> dict[str, object]:
    memory_class = MEMORY_CLASS_EMOTIONAL_PATTERN
    if pattern.category == "relationships":
        memory_class = MEMORY_CLASS_RELATIONSHIP
    elif pattern.category in {"goals", "active_projects", "work"}:
        memory_class = MEMORY_CLASS_ACTIVE_PROJECT
    return {
        "memory_class": memory_class,
        "emotional_salience": pattern.confidence if pattern.category == "emotional_patterns" else 0.35,
        "stability_class": STABILITY_STABLE if pattern.confidence >= 0.8 else STABILITY_EVOLVING,
        "decay_class": DECAY_SLOW,
        "relationship_proximity": 0.3 if pattern.category == "relationships" else 0.0,
        "evidence_strength": pattern.confidence,
    }


def _importance_for_confidence(confidence: float) -> int:
    if confidence >= 0.9:
        return 5
    if confidence >= 0.75:
        return 4
    return 3


def _latest_episode_observed_at(
    db: Session,
    *,
    user_id: int,
    episode_ids: Sequence[int],
) -> datetime:
    episodes = list(
        db.scalars(
            select(MemoryEpisode).where(
                MemoryEpisode.user_id == user_id,
                MemoryEpisode.id.in_(list(episode_ids)),
            )
        ).all()
    )
    if not episodes:
        return datetime.now(UTC)
    return max(_episode_datetime(episode) for episode in episodes)


def _evidence_text(pattern: PatternCandidate) -> str:
    lines = [f"- {line}" for line in pattern.evidence]
    return "\n".join(lines)


def _normalize_output_category(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
        if normalized in _ALLOWED_OUTPUT_CATEGORIES:
            return normalized
    return "emotional_patterns"


def _clean_text(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.strip().split())
    if len(text) > limit:
        text = text[:limit].rstrip()
    return text


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _int_tuple(value: object) -> tuple[int, ...]:
    result: list[int] = []
    for item in _as_list(value):
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in result:
            result.append(parsed)
    return tuple(result)


def _bounded_float(value: object, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))
