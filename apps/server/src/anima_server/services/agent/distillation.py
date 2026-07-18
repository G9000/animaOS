"""IL5 — Forgetting as distillation (F7 extension).

When passive heat decay (F2/F7) takes a ``casual``, ``transient``, or
``emotional_pattern`` item below the visibility floor, this module folds its
affective/topical signature into a semantic ``tendency`` claim instead of
letting it silently rot: a numeric-only ``TendencyContribution`` ledger row
links the tombstoned item to the claim, and the item's content/embedding/
evidence are gutted in place (id, memory_class, category, and created_at
survive as the tombstone).

Zero LLM calls anywhere in this module (PRD §5 Architecture Rules) — the
tendency phrase is a deterministic template, and contribution strength is
plain arithmetic over existing salience fields.

``identity``, ``life_event``, ``relationship`` (and any other memory_class,
e.g. ``active_project``) are exempt and keep unchanged F7 semantics — see
``DISTILL_MEMORY_CLASSES``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from anima_server.models import ForgetAuditLog, MemoryItem, MemoryItemEvidence, TendencyContribution
from anima_server.services.agent.claims import upsert_tendency_claim
from anima_server.services.agent.forgetting import HEAT_VISIBILITY_FLOOR
from anima_server.services.agent.memory_salience import (
    MEMORY_CLASS_CASUAL,
    MEMORY_CLASS_EMOTIONAL_PATTERN,
    MEMORY_CLASS_TRANSIENT,
)
from anima_server.services.data_crypto import df, ef

logger = logging.getLogger(__name__)

# PRD-fixed distill set (not configurable — see PRD "IL5 Forgetting as
# Distillation"). Everything else (identity, life_event, relationship,
# active_project, ...) follows existing F7 semantics untouched.
DISTILL_MEMORY_CLASSES = frozenset(
    {MEMORY_CLASS_CASUAL, MEMORY_CLASS_TRANSIENT, MEMORY_CLASS_EMOTIONAL_PATTERN}
)

DISTILLED_AUDIT_TRIGGER = "passive_decay"
DISTILLED_AUDIT_SCOPE = "distilled"


@dataclass(slots=True)
class DistillationResult:
    distilled: int = 0
    failed: int = 0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _contribution_vector_for_item(item: MemoryItem) -> dict[str, float]:
    """Numeric-only signature deltas for the ledger — never content.

    ``strength`` is normalized importance (how strong a signal this item
    was); ``valence_hint`` reuses the item's own emotional_salience field
    (the same affective magnitude signal F1/F2 already compute). Both are
    plain arithmetic over existing salience fields, per PRD §5 (no LLM).
    """
    strength = _clamp01(float(item.importance or 0) / 5.0)
    valence_hint = _clamp01(float(item.emotional_salience or 0.0))
    return {"strength": round(strength, 4), "valence_hint": round(valence_hint, 4)}


def recompute_tendency_from_ledger(
    db: Session,
    *,
    tendency_claim_id: int,
) -> dict[str, float] | None:
    """Single source of truth for a tendency claim's aggregate strength.

    Recomputed as the MEAN (not sum — a sum would grow unboundedly with
    every new contributor and stop meaning "how strong is this tendency")
    of ``strength``/``valence_hint`` across the claim's surviving
    ``TendencyContribution`` rows. Both the distill path and the
    right-to-forget path (``forgetting.forget_memory``) call this exact
    function so a single contribution can always be removed exactly.

    Returns ``None`` when no ledger rows survive — the caller must delete
    the claim in that case (a tendency with zero contributors is not a
    tendency).
    """
    rows = list(
        db.scalars(
            select(TendencyContribution).where(
                TendencyContribution.tendency_claim_id == tendency_claim_id
            )
        ).all()
    )
    if not rows:
        return None

    strengths = [float(row.contribution_vector.get("strength", 0.0)) for row in rows]
    valences = [float(row.contribution_vector.get("valence_hint", 0.0)) for row in rows]
    return {
        "strength": round(sum(strengths) / len(strengths), 4),
        "valence_hint": round(sum(valences) / len(valences), 4),
        "contributor_count": len(rows),
    }


def _distill_one_item(
    db: Session,
    *,
    item: MemoryItem,
    user_id: int,
    now: datetime,
) -> None:
    """Distill one item: claim + ledger + tombstone + audit, atomically.

    Caller (``distill_due_items``) commits or rolls back the whole thing —
    a failure here must never leave a half-written tombstone (no claim/
    ledger) or a half-written ledger (no gutted content).
    """
    content_plain = df(user_id, item.content, table="memory_items", field="content")
    contribution_vector = _contribution_vector_for_item(item)

    claim = upsert_tendency_claim(
        db,
        user_id=user_id,
        category=item.category,
        memory_class=item.memory_class,
        topic_content=content_plain,
    )

    db.add(
        TendencyContribution(
            user_id=user_id,
            tombstone_item_id=item.id,
            tendency_claim_id=claim.id,
            contribution_vector=contribution_vector,
            created_at=now,
        )
    )
    db.flush()

    aggregate = recompute_tendency_from_ledger(db, tendency_claim_id=claim.id)
    # Cannot be None — we just flushed a row for this claim above.
    claim.value_json = aggregate
    claim.updated_at = now

    # Tombstone: gut content via the normal encrypted-write path (empty
    # string round-trips to "" regardless of whether a DEK is active), null
    # the embedding, hard-delete evidence rows. memory_class/category/
    # created_at are left untouched (the retained tombstone shell).
    item.content = ef(user_id, "", table="memory_items", field="content")
    item.embedding_json = None
    item.embedding_checksum = None
    item.distilled_at = now

    evidence_deleted = db.execute(
        delete(MemoryItemEvidence).where(
            MemoryItemEvidence.user_id == user_id,
            MemoryItemEvidence.memory_item_id == item.id,
        )
    ).rowcount or 0

    db.add(
        ForgetAuditLog(
            user_id=user_id,
            forgotten_at=now,
            trigger=DISTILLED_AUDIT_TRIGGER,
            scope=DISTILLED_AUDIT_SCOPE,
            items_forgotten=1,
            derived_refs_affected=int(evidence_deleted),
        )
    )
    db.flush()

    _schedule_distill_external_cleanup_after_commit(db, user_id=user_id, item_id=item.id)


def _schedule_distill_external_cleanup_after_commit(
    db: Session,
    *,
    user_id: int,
    item_id: int,
) -> None:
    """Remove the now-gutted item from the vector/BM25 retrieval indexes
    once this distillation commits — reuses forget_memory's exact cleanup
    helper (same operation: an item id must stop being searchable)."""
    from anima_server.services.agent.forgetting import (
        _schedule_forget_external_cleanup_after_commit,
    )

    _schedule_forget_external_cleanup_after_commit(db, user_id=user_id, item_ids=[item_id])


def distill_due_items(
    db: Session,
    *,
    user_id: int,
    max_per_run: int,
    now: datetime | None = None,
) -> DistillationResult:
    """Sweep sub-floor casual/transient/emotional_pattern items into
    tendency claims (PRD IL5).

    Called from the F7 heat-decay sleep task (``sleep_agent._task_heat_decay``)
    AFTER heat has already been recomputed and committed for this sweep — a
    distillation failure must never roll back heat-decay's own updates.
    Per-item transactional isolation mirrors IL4's crystallization loop
    (``latent_traces.crystallize_due_traces``): each item commits or rolls
    back on its own, so one bad item never aborts the rest of the sweep.
    Idempotent: already-distilled (``distilled_at`` set), superseded, and
    exempt-class items are excluded by the query itself.
    """
    ref_now = now or datetime.now(UTC)
    result = DistillationResult()

    due = list(
        db.scalars(
            select(MemoryItem)
            .where(
                MemoryItem.user_id == user_id,
                MemoryItem.superseded_by.is_(None),
                MemoryItem.distilled_at.is_(None),
                MemoryItem.memory_class.in_(DISTILL_MEMORY_CLASSES),
                MemoryItem.heat < HEAT_VISIBILITY_FLOOR,
            )
            .order_by(MemoryItem.heat.asc(), MemoryItem.id.asc())
            .limit(max(0, max_per_run))
        ).all()
    )

    for item in due:
        try:
            _distill_one_item(db, item=item, user_id=user_id, now=ref_now)
        except Exception:
            logger.exception(
                "IL5 distillation failed for memory item %s (user %s)",
                item.id,
                user_id,
            )
            db.rollback()
            result.failed += 1
            continue
        db.commit()
        result.distilled += 1

    return result
