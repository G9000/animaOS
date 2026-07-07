"""Heat-based memory scoring — F2.

Persistent heat score combining access frequency, interaction depth,
time-decay, and LLM-assigned importance. Hot memories surface first;
cold memories are candidates for archival.

Formula:
    H = alpha * access_count + beta * interaction_depth
        + gamma * recency_decay + delta * importance

Where recency_decay = exp(-hours_since_last_access / tau).
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.services import anima_core_bindings

logger = logging.getLogger(__name__)

# ── Configurable weights ────────────────────────────────────────────
HEAT_ALPHA: float = 1.0  # access count weight
HEAT_BETA: float = 1.0  # interaction depth weight
HEAT_GAMMA: float = 1.0  # recency decay weight
HEAT_DELTA: float = 0.5  # importance weight
RECENCY_TAU_HOURS: float = 24.0  # time-decay constant
MAX_IMPORTANCE: int = 5
HEAT_IMPORTANCE_FLOOR_SCALE: float = 0.03  # heat floor per importance point

# Retrieval treats heat == 0.0 (and NULL) as "never scored" and keeps the
# item visible (see memory_store visibility scan and the hybrid/semantic
# retrieval floors).  A scored item that fully decays would otherwise land on
# exactly 0.0 via float underflow and silently *bypass* the visibility floor,
# resurfacing forgotten memories.  Clamp every scored heat to a tiny positive
# epsilon (well below HEAT_VISIBILITY_FLOOR = 0.01) so "scored-to-zero" stays
# distinguishable from "never scored": decayed items sit just above 0.0 but
# below the floor, so the floor filters them out.
HEAT_SCORED_EPSILON: float = 1e-6


def importance_heat_floor(importance: float) -> float:
    """Minimum heat for an item based on its importance, independent of
    recency decay.

    Every other heat term is multiplied by recency, so without a floor an
    importance-5 standing fact ("I'm diabetic") falls below the retrieval
    visibility floor (0.01) after a few idle days and silently stops being
    surfaced.  The floor keeps importance >= 1 above the visibility floor
    and importance >= 4 above the default archival threshold (0.1), while
    leaving low/mid-importance items eligible for normal forgetting.
    """
    if importance <= 0:
        return 0.0
    return HEAT_IMPORTANCE_FLOOR_SCALE * min(float(importance), float(MAX_IMPORTANCE))


def compute_time_decay(
    last_accessed: datetime,
    now: datetime,
    *,
    tau_hours: float = RECENCY_TAU_HOURS,
) -> float:
    """Exponential time decay: exp(-hours_since / tau)."""
    if last_accessed.tzinfo is None:
        last_accessed = last_accessed.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    hours = max(0.0, (now - last_accessed).total_seconds() / 3600.0)
    return math.exp(-hours / tau_hours)


def compute_heat(
    *,
    access_count: int,
    interaction_depth: int,
    last_accessed_at: datetime | None,
    importance: float = 0.0,
    now: datetime | None = None,
    created_at: datetime | None = None,
    tau_hours: float = RECENCY_TAU_HOURS,
    superseded: bool = False,
    decay_class: str | None = None,
    emotional_salience: float = 0.0,
    relationship_proximity: float = 0.0,
    evidence_strength: float = 0.8,
) -> float:
    """Compute heat: H = alpha*access + beta*depth + gamma*recency + delta*importance.

    For the recency component, ``last_accessed_at`` is preferred.  When it is
    ``None`` (item never accessed), ``created_at`` is used as a fallback so
    that freshly-created items still receive a recency signal.

    ``tau_hours`` controls the time-decay rate (default: ``RECENCY_TAU_HOURS``).
    Superseded items pass a smaller tau for faster decay; they are also
    exempt from the importance floor so they can fully decay.
    """
    ref_now = now or datetime.now(UTC)
    from anima_server.services.agent.memory_salience import (
        DECAY_STANDARD,
        apply_decay_class_to_tau,
        salience_heat_floor_multiplier,
    )

    effective_decay_class = (decay_class or DECAY_STANDARD).strip().lower()
    tau_hours = apply_decay_class_to_tau(tau_hours, effective_decay_class)
    floor = 0.0 if superseded else importance_heat_floor(importance)
    if floor > 0.0:
        floor *= salience_heat_floor_multiplier(
            emotional_salience=emotional_salience,
            relationship_proximity=relationship_proximity,
            evidence_strength=evidence_strength,
        )
    recency = 0.0
    recency_ref = last_accessed_at or created_at
    uses_salience_adjustments = (
        effective_decay_class != DECAY_STANDARD
        or emotional_salience > 0.0
        or relationship_proximity > 0.0
        or abs(evidence_strength - 0.8) > 1e-9
    )
    if (
        recency_ref is not None
        and tau_hours == RECENCY_TAU_HOURS
        and not uses_salience_adjustments
        and anima_core_bindings.rust_compute_heat is not None
        and float(importance).is_integer()
        and 0 <= int(importance) <= MAX_IMPORTANCE
    ):
        if recency_ref.tzinfo is None:
            recency_ref = recency_ref.replace(tzinfo=UTC)
        if ref_now.tzinfo is None:
            ref_now = ref_now.replace(tzinfo=UTC)
        seconds_since_access = max(0.0, (ref_now - recency_ref).total_seconds())
        try:
            return max(
                float(
                    anima_core_bindings.rust_compute_heat(
                        access_count=access_count,
                        interaction_depth=interaction_depth,
                        importance=importance,
                        seconds_since_access=seconds_since_access,
                        superseded=superseded,
                    )
                ),
                floor,
                HEAT_SCORED_EPSILON,
            )
        except Exception:
            logger.debug("Rust heat scoring failed; falling back to Python", exc_info=True)

    if recency_ref is not None:
        recency = compute_time_decay(recency_ref, ref_now, tau_hours=tau_hours)
    heat = (
        HEAT_ALPHA * access_count + HEAT_BETA * interaction_depth + HEAT_DELTA * importance
    ) * recency + HEAT_GAMMA * recency
    # A scored item never lands on exactly 0.0 — see HEAT_SCORED_EPSILON.
    return max(heat, floor, HEAT_SCORED_EPSILON)


def compute_heat_for_item(
    item: Any,
    *,
    now: datetime | None = None,
    tau_hours: float = RECENCY_TAU_HOURS,
    superseded: bool | None = None,
) -> float:
    ref_count = getattr(item, "reference_count", 0) or 0
    evidence_strength = getattr(item, "evidence_strength", None)
    is_superseded = (
        getattr(item, "superseded_by", None) is not None
        if superseded is None
        else superseded
    )
    return compute_heat(
        access_count=ref_count,
        interaction_depth=ref_count,
        last_accessed_at=getattr(item, "last_referenced_at", None),
        importance=float(getattr(item, "importance", 3) or 3),
        now=now,
        created_at=getattr(item, "created_at", None),
        tau_hours=tau_hours,
        superseded=is_superseded,
        decay_class=getattr(item, "decay_class", None),
        emotional_salience=float(getattr(item, "emotional_salience", 0.0) or 0.0),
        relationship_proximity=float(getattr(item, "relationship_proximity", 0.0) or 0.0),
        evidence_strength=0.8 if evidence_strength is None else float(evidence_strength),
    )


def update_heat_on_access(
    db: Session,
    items: list[Any],
    *,
    now: datetime | None = None,
) -> None:
    """Recompute and persist heat for accessed items.

    Called after touch_memory_items() has already incremented
    reference_count and updated last_referenced_at.
    """
    ref_now = now or datetime.now(UTC)
    for item in items:
        item.heat = compute_heat_for_item(item, now=ref_now)
    db.flush()


def decay_all_heat(
    db: Session,
    *,
    user_id: int,
    now: datetime | None = None,
) -> int:
    """Batch-update heat for all active items. Called during sleep tasks.

    Returns count of items updated.
    """
    from anima_server.models import MemoryItem

    ref_now = now or datetime.now(UTC)
    from anima_server.services.agent.forgetting import SUPERSEDED_DECAY_MULTIPLIER

    # Decay both active and superseded items
    items = list(
        db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
            )
        ).all()
    )

    for item in items:
        # Superseded items decay 3x faster (lower tau)
        superseded = item.superseded_by is not None
        tau = RECENCY_TAU_HOURS
        if superseded:
            tau = RECENCY_TAU_HOURS / SUPERSEDED_DECAY_MULTIPLIER
        item.heat = compute_heat_for_item(
            item,
            now=ref_now,
            tau_hours=tau,
            superseded=superseded,
        )
    db.flush()
    return len(items)


def get_hottest_items(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
    category: str | None = None,
) -> list[Any]:
    """Return items sorted by heat descending."""
    from anima_server.models import MemoryItem

    stmt = select(MemoryItem).where(
        MemoryItem.user_id == user_id,
        MemoryItem.superseded_by.is_(None),
    )
    if category is not None:
        stmt = stmt.where(MemoryItem.category == category)
    stmt = stmt.order_by(MemoryItem.heat.desc()).limit(limit)
    return list(db.scalars(stmt).all())


def get_coldest_items(
    db: Session,
    *,
    user_id: int,
    limit: int = 20,
    heat_threshold: float = 0.1,
) -> list[Any]:
    """Return items below heat threshold (candidates for archival)."""
    from anima_server.models import MemoryItem

    return list(
        db.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.user_id == user_id,
                MemoryItem.superseded_by.is_(None),
                MemoryItem.heat < heat_threshold,
            )
            .order_by(MemoryItem.heat.asc())
            .limit(limit)
        ).all()
    )
