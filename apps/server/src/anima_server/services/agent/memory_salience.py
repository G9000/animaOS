from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

MEMORY_CLASS_IDENTITY = "identity"
MEMORY_CLASS_LIFE_EVENT = "life_event"
MEMORY_CLASS_RELATIONSHIP = "relationship"
MEMORY_CLASS_ACTIVE_PROJECT = "active_project"
MEMORY_CLASS_CASUAL = "casual"
MEMORY_CLASS_TRANSIENT = "transient"
MEMORY_CLASS_EMOTIONAL_PATTERN = "emotional_pattern"

VALID_MEMORY_CLASSES = frozenset(
    {
        MEMORY_CLASS_IDENTITY,
        MEMORY_CLASS_LIFE_EVENT,
        MEMORY_CLASS_RELATIONSHIP,
        MEMORY_CLASS_ACTIVE_PROJECT,
        MEMORY_CLASS_CASUAL,
        MEMORY_CLASS_TRANSIENT,
        MEMORY_CLASS_EMOTIONAL_PATTERN,
    }
)

STABILITY_STABLE = "stable"
STABILITY_EVOLVING = "evolving"
STABILITY_TEMPORARY = "temporary"
VALID_STABILITY_CLASSES = frozenset(
    {STABILITY_STABLE, STABILITY_EVOLVING, STABILITY_TEMPORARY}
)

DECAY_ANCHORED = "anchored"
DECAY_SLOW = "slow"
DECAY_STANDARD = "standard"
DECAY_FAST = "fast"
DECAY_EPHEMERAL = "ephemeral"
VALID_DECAY_CLASSES = frozenset(
    {DECAY_ANCHORED, DECAY_SLOW, DECAY_STANDARD, DECAY_FAST, DECAY_EPHEMERAL}
)

DECAY_TAU_MULTIPLIERS: dict[str, float] = {
    DECAY_ANCHORED: 12.0,
    DECAY_SLOW: 5.0,
    DECAY_STANDARD: 1.0,
    DECAY_FAST: 0.35,
    DECAY_EPHEMERAL: 0.08,
}

_DECAY_STRENGTH = {
    DECAY_ANCHORED: 5,
    DECAY_SLOW: 4,
    DECAY_STANDARD: 3,
    DECAY_FAST: 2,
    DECAY_EPHEMERAL: 1,
}
_STABILITY_STRENGTH = {
    STABILITY_STABLE: 3,
    STABILITY_EVOLVING: 2,
    STABILITY_TEMPORARY: 1,
}
_CLASS_STRENGTH = {
    MEMORY_CLASS_IDENTITY: 7,
    MEMORY_CLASS_LIFE_EVENT: 6,
    MEMORY_CLASS_RELATIONSHIP: 5,
    MEMORY_CLASS_EMOTIONAL_PATTERN: 4,
    MEMORY_CLASS_ACTIVE_PROJECT: 3,
    MEMORY_CLASS_CASUAL: 2,
    MEMORY_CLASS_TRANSIENT: 1,
}

_SALIENCE_SIGNAL_FIELDS = frozenset(
    {
        "memory_class",
        "class",
        "emotional_salience",
        "emotional",
        "stability_class",
        "stability",
        "decay_class",
        "decay",
        "relationship_proximity",
        "proximity",
        "evidence_strength",
        "evidence",
    }
)


@dataclass(frozen=True, slots=True)
class MemorySalience:
    memory_class: str = MEMORY_CLASS_CASUAL
    emotional_salience: float = 0.0
    stability_class: str = STABILITY_STABLE
    decay_class: str = DECAY_STANDARD
    relationship_proximity: float = 0.0
    evidence_strength: float = 0.8


@dataclass(frozen=True, slots=True)
class SoftEvolution:
    kind: str
    reason: str


def normalize_salience_payload(
    payload: Mapping[str, Any] | None,
    *,
    content: str,
    category: str,
    importance: int | float = 3,
    emotion: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Return bounded, schema-stable salience metadata."""
    inferred = _infer_salience(content=content, category=category, importance=importance)
    raw = payload if isinstance(payload, Mapping) else {}

    emotion_confidence = _bounded_float((emotion or {}).get("confidence"), default=0.0)
    emotional_default = max(inferred.emotional_salience, emotion_confidence)
    evidence_default = max(inferred.evidence_strength, emotion_confidence or 0.0)

    memory_class = _normalized_choice(
        raw.get("memory_class") or raw.get("class"),
        VALID_MEMORY_CLASSES,
        inferred.memory_class,
    )
    stability_class = _normalized_choice(
        raw.get("stability_class") or raw.get("stability"),
        VALID_STABILITY_CLASSES,
        inferred.stability_class,
    )
    decay_class = _normalized_choice(
        raw.get("decay_class") or raw.get("decay"),
        VALID_DECAY_CLASSES,
        _default_decay_class(memory_class, stability_class, inferred.decay_class),
    )

    signal_fields = sorted(key for key in raw if key in _SALIENCE_SIGNAL_FIELDS)
    salience = MemorySalience(
        memory_class=memory_class,
        emotional_salience=_bounded_float(
            _first_present(raw, "emotional_salience", "emotional"),
            default=emotional_default,
        ),
        stability_class=stability_class,
        decay_class=decay_class,
        relationship_proximity=_bounded_float(
            _first_present(raw, "relationship_proximity", "proximity"),
            default=inferred.relationship_proximity,
        ),
        evidence_strength=_bounded_float(
            _first_present(raw, "evidence_strength", "evidence"),
            default=evidence_default,
        ),
    )
    serialized = serialize_memory_salience(salience)
    serialized["salience_source"] = "explicit" if signal_fields else "inferred"
    serialized["salience_signal_fields"] = signal_fields
    return serialized


def coerce_memory_salience(
    value: Mapping[str, Any] | MemorySalience | None,
    *,
    content: str = "",
    category: str = "fact",
    importance: int | float = 3,
) -> MemorySalience:
    if isinstance(value, MemorySalience):
        return value
    normalized = normalize_salience_payload(
        value if isinstance(value, Mapping) else None,
        content=content,
        category=category,
        importance=importance,
    )
    return MemorySalience(
        memory_class=str(normalized["memory_class"]),
        emotional_salience=float(normalized["emotional_salience"]),
        stability_class=str(normalized["stability_class"]),
        decay_class=str(normalized["decay_class"]),
        relationship_proximity=float(normalized["relationship_proximity"]),
        evidence_strength=float(normalized["evidence_strength"]),
    )


def serialize_memory_salience(salience: MemorySalience) -> dict[str, object]:
    return {
        "memory_class": salience.memory_class,
        "emotional_salience": round(float(salience.emotional_salience), 4),
        "stability_class": salience.stability_class,
        "decay_class": salience.decay_class,
        "relationship_proximity": round(float(salience.relationship_proximity), 4),
        "evidence_strength": round(float(salience.evidence_strength), 4),
    }


def memory_salience_model_kwargs(
    value: Mapping[str, Any] | MemorySalience | None,
    *,
    content: str,
    category: str,
    importance: int | float = 3,
) -> dict[str, object]:
    salience = coerce_memory_salience(
        value,
        content=content,
        category=category,
        importance=importance,
    )
    return {
        "memory_class": salience.memory_class,
        "emotional_salience": salience.emotional_salience,
        "stability_class": salience.stability_class,
        "decay_class": salience.decay_class,
        "relationship_proximity": salience.relationship_proximity,
        "evidence_strength": salience.evidence_strength,
    }


def item_salience(item: Any) -> MemorySalience:
    return MemorySalience(
        memory_class=_normalized_choice(
            getattr(item, "memory_class", None),
            VALID_MEMORY_CLASSES,
            MEMORY_CLASS_CASUAL,
        ),
        emotional_salience=_bounded_float(getattr(item, "emotional_salience", None)),
        stability_class=_normalized_choice(
            getattr(item, "stability_class", None),
            VALID_STABILITY_CLASSES,
            STABILITY_STABLE,
        ),
        decay_class=_normalized_choice(
            getattr(item, "decay_class", None),
            VALID_DECAY_CLASSES,
            DECAY_STANDARD,
        ),
        relationship_proximity=_bounded_float(
            getattr(item, "relationship_proximity", None)
        ),
        evidence_strength=_bounded_float(
            getattr(item, "evidence_strength", None),
            default=0.8,
        ),
    )


def merge_salience(
    existing: Mapping[str, Any] | MemorySalience | None,
    incoming: Mapping[str, Any] | MemorySalience | None,
) -> MemorySalience:
    old = coerce_memory_salience(existing)
    new = coerce_memory_salience(incoming)
    emotional = old.emotional_salience + (
        new.emotional_salience * (1.0 - old.emotional_salience) * 0.65
    )
    if 0.2 <= new.emotional_salience < 0.5:
        emotional += 0.05

    return MemorySalience(
        memory_class=_stronger_choice(
            old.memory_class,
            new.memory_class,
            strengths=_CLASS_STRENGTH,
        ),
        emotional_salience=min(1.0, emotional),
        stability_class=_stronger_choice(
            old.stability_class,
            new.stability_class,
            strengths=_STABILITY_STRENGTH,
        ),
        decay_class=_stronger_choice(
            old.decay_class,
            new.decay_class,
            strengths=_DECAY_STRENGTH,
        ),
        relationship_proximity=max(old.relationship_proximity, new.relationship_proximity),
        evidence_strength=min(1.0, old.evidence_strength + new.evidence_strength * 0.25),
    )


def merge_salience_into_item(
    item: Any,
    incoming: Mapping[str, Any] | MemorySalience | None,
) -> MemorySalience:
    merged = merge_salience(item_salience(item), incoming)
    item.memory_class = merged.memory_class
    item.emotional_salience = merged.emotional_salience
    item.stability_class = merged.stability_class
    item.decay_class = merged.decay_class
    item.relationship_proximity = merged.relationship_proximity
    item.evidence_strength = merged.evidence_strength
    item.updated_at = datetime.now(UTC)
    if hasattr(item, "heat"):
        from anima_server.services.agent.heat_scoring import compute_heat_for_item

        item.heat = compute_heat_for_item(item)
    return merged


def apply_decay_class_to_tau(tau_hours: float, decay_class: str | None) -> float:
    multiplier = DECAY_TAU_MULTIPLIERS.get(
        (decay_class or DECAY_STANDARD).strip().lower(),
        DECAY_TAU_MULTIPLIERS[DECAY_STANDARD],
    )
    return max(0.1, float(tau_hours) * multiplier)


def salience_heat_floor_multiplier(
    *,
    emotional_salience: float = 0.0,
    relationship_proximity: float = 0.0,
    evidence_strength: float = 0.8,
) -> float:
    emotional = _bounded_float(emotional_salience)
    proximity = _bounded_float(relationship_proximity)
    evidence = _bounded_float(evidence_strength, default=0.8)
    return max(0.1, 1.0 + emotional * 0.45 + proximity * 0.2 + (evidence - 0.8) * 0.2)


def detect_soft_evolution(
    *,
    category: str,
    existing_salience: Mapping[str, Any] | MemorySalience | None = None,
    incoming_salience: Mapping[str, Any] | MemorySalience | None = None,
) -> SoftEvolution | None:
    if category not in {"preference", "relationship"}:
        return None

    if not _has_explicit_salience_signal(incoming_salience):
        return None

    existing = coerce_memory_salience(existing_salience)
    incoming = coerce_memory_salience(incoming_salience)
    if incoming.evidence_strength < 0.5:
        return None

    if incoming.memory_class == MEMORY_CLASS_TRANSIENT or incoming.decay_class == DECAY_EPHEMERAL:
        return None

    is_evolving = incoming.stability_class == STABILITY_EVOLVING
    is_salient_emotional_shift = (
        incoming.memory_class in {MEMORY_CLASS_RELATIONSHIP, MEMORY_CLASS_EMOTIONAL_PATTERN}
        and incoming.emotional_salience > existing.emotional_salience
    )
    is_relationship_proximity_shift = (
        category == "relationship"
        and incoming.relationship_proximity != existing.relationship_proximity
    )
    if not (is_evolving or is_salient_emotional_shift or is_relationship_proximity_shift):
        return None

    if category == "preference":
        return SoftEvolution(
            kind="preference_drift",
            reason="structured salience marks matched preference as evolving",
        )
    return SoftEvolution(
        kind="relationship_drift",
        reason="structured salience marks matched relationship as evolving",
    )


def surface_memory_drift(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
) -> dict[str, object]:
    """Return a sleep-time report for linked and possible memory evolution."""
    from anima_server.models import MemoryItem

    linked = list(
        db.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.user_id == user_id,
                MemoryItem.evolves_from_item_id.is_not(None),
            )
            .order_by(MemoryItem.created_at.desc())
            .limit(limit)
        ).all()
    )
    linked_report = [
        {
            "item_id": int(item.id),
            "evolves_from_item_id": int(item.evolves_from_item_id),
            "kind": item.evolution_kind or "soft_evolution",
            "category": item.category,
        }
        for item in linked
        if item.evolves_from_item_id is not None
    ]
    return {
        "linked_evolution_count": len(linked_report),
        "possible_drift_count": 0,
        "linked_evolution": linked_report,
        "possible_drift": [],
        "possible_drift_method": "disabled_without_structured_evolution_signal",
    }


def _infer_salience(
    *,
    content: str,
    category: str,
    importance: int | float,
) -> MemorySalience:
    bounded_importance = max(1.0, min(5.0, float(importance or 3)))
    if category == "relationship":
        return MemorySalience(
            memory_class=MEMORY_CLASS_RELATIONSHIP,
            emotional_salience=0.35,
            stability_class=STABILITY_EVOLVING,
            decay_class=DECAY_SLOW,
            relationship_proximity=0.65,
            evidence_strength=0.8,
        )
    if category == "fact" and bounded_importance >= 5:
        return MemorySalience(
            memory_class=MEMORY_CLASS_IDENTITY,
            emotional_salience=0.2,
            stability_class=STABILITY_STABLE,
            decay_class=DECAY_ANCHORED,
            evidence_strength=0.9,
        )
    if category == "goal":
        return MemorySalience(
            memory_class=MEMORY_CLASS_ACTIVE_PROJECT,
            emotional_salience=0.1,
            stability_class=STABILITY_EVOLVING,
            decay_class=DECAY_STANDARD,
            evidence_strength=0.8,
        )
    if category == "preference" and bounded_importance >= 4:
        return MemorySalience(
            memory_class=MEMORY_CLASS_CASUAL,
            emotional_salience=0.15,
            stability_class=STABILITY_EVOLVING,
            decay_class=DECAY_STANDARD,
            evidence_strength=0.8,
        )
    if bounded_importance <= 2:
        return MemorySalience(
            memory_class=MEMORY_CLASS_CASUAL,
            emotional_salience=0.0,
            stability_class=STABILITY_TEMPORARY,
            decay_class=DECAY_FAST,
            evidence_strength=0.7,
        )
    return MemorySalience()


def _default_decay_class(
    memory_class: str,
    stability_class: str,
    fallback: str,
) -> str:
    if memory_class in {MEMORY_CLASS_IDENTITY, MEMORY_CLASS_LIFE_EVENT}:
        return DECAY_ANCHORED
    if memory_class == MEMORY_CLASS_RELATIONSHIP:
        return DECAY_SLOW
    if memory_class == MEMORY_CLASS_TRANSIENT or stability_class == STABILITY_TEMPORARY:
        return DECAY_EPHEMERAL
    return fallback


def _normalized_choice(value: object, choices: frozenset[str], default: str) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in choices:
            return normalized
    return default


def _first_present(raw: Mapping[str, Any], *keys: str) -> object:
    for key in keys:
        if key in raw:
            return raw[key]
    return None


def _has_explicit_salience_signal(
    value: Mapping[str, Any] | MemorySalience | None,
) -> bool:
    if isinstance(value, MemorySalience):
        return True
    if not isinstance(value, Mapping):
        return False
    if "salience_source" in value:
        if value.get("salience_source") != "explicit":
            return False
        fields = value.get("salience_signal_fields")
        return isinstance(fields, list) and len(fields) > 0
    return bool(set(value) & _SALIENCE_SIGNAL_FIELDS)


def _bounded_float(value: object, *, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))


def _stronger_choice(first: str, second: str, *, strengths: dict[str, int]) -> str:
    if strengths.get(second, 0) > strengths.get(first, 0):
        return second
    return first
