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

logger = logging.getLogger(__name__)

# ── Configurable weights ────────────────────────────────────────────
HEAT_ALPHA: float = 1.0        # access count weight
HEAT_BETA: float = 1.0         # interaction depth weight
HEAT_GAMMA: float = 1.0        # recency decay weight
HEAT_DELTA: float = 0.5        # importance weight
RECENCY_TAU_HOURS: float = 24.0  # time-decay constant


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
) -> float:
    """Compute heat: H = alpha*access + beta*depth + gamma*recency + delta*importance.

    For the recency component, ``last_accessed_at`` is preferred.  When it is
    ``None`` (item never accessed), ``created_at`` is used as a fallback so
    that freshly-created items still receive a recency signal.
    """
    ref_now = now or datetime.now(UTC)
    recency = 0.0
    recency_ref = last_accessed_at or created_at
    if recency_ref is not None:
        recency = compute_time_decay(recency_ref, ref_now)
    return (
        HEAT_ALPHA * access_count
        + HEAT_BETA * interaction_depth
        + HEAT_GAMMA * recency
        + HEAT_DELTA * importance
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
        ref_count = item.reference_count or 0
        item.heat = compute_heat(
            access_count=ref_count,
            interaction_depth=ref_count,  # proxied by ref_count in v1
            last_accessed_at=item.last_referenced_at,
            importance=float(item.importance),
            now=ref_now,
            created_at=item.created_at,
        )
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
    items = list(
        db.scalars(
            select(MemoryItem).where(
                MemoryItem.user_id == user_id,
                MemoryItem.superseded_by.is_(None),
            )
        ).all()
    )

    for item in items:
        ref_count = item.reference_count or 0
        item.heat = compute_heat(
            access_count=ref_count,
            interaction_depth=ref_count,
            last_accessed_at=item.last_referenced_at,
            importance=float(item.importance),
            now=ref_now,
            created_at=item.created_at,
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
