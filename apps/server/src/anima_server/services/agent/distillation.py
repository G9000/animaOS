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

from anima_server.models import (
    ForgetAuditLog,
    MemoryClaim,
    MemoryClaimEvidence,
    MemoryItem,
    MemoryItemEvidence,
    MemoryItemTag,
    TendencyContribution,
)
from anima_server.services.agent.claims import upsert_tendency_claim
from anima_server.services.agent.heat_scoring import (
    MAX_IMPORTANCE,
    importance_heat_floor,
)
from anima_server.services.agent.memory_salience import (
    MEMORY_CLASS_CASUAL,
    MEMORY_CLASS_EMOTIONAL_PATTERN,
    MEMORY_CLASS_TRANSIENT,
    salience_heat_floor_multiplier,
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

# ── Eligibility: "fully decayed to its own salience floor" ──────────────
#
# The PRD phrase "below the visibility floor" is UNREACHABLE for
# schema-valid items by design: ``compute_heat`` clamps every
# non-superseded scored item to ``importance_heat_floor(importance) x
# salience_heat_floor_multiplier(...)``, and the multiplier's practical
# minimum is 0.84 (only the evidence term can reduce it below 1.0), so
# even importance-1 floors at ~0.0252 > HEAT_VISIBILITY_FLOOR (0.01).
# The honest trigger semantic is therefore: distill when an item has
# FULLY DECAYED TO ITS OWN FLOOR — the recency/access contribution is
# gone and only the floor clamp keeps it visible.
#
# The check runs in two stages:
# 1. A cheap SQL band pre-filter: 0 < heat <= _MAX_ITEM_HEAT_FLOOR, where
#    the ceiling is the maximum floor any schema-valid item can have
#    (importance_heat_floor(5) x the multiplier's maximum 1.69 =
#    0.03·5·1.69 = 0.2535), computed below from the two REAL functions,
#    never duplicated arithmetic. heat == 0/NULL means "never scored" and
#    is excluded (scoring happens in decay_all_heat immediately before
#    this sweep runs).
# 2. An exact Python check per candidate: heat <= its OWN floor + epsilon,
#    with the floor rebuilt from the item's actual salience fields via the
#    same two functions ``compute_heat`` uses, so the comparison is
#    bit-exact against what the decay pass just wrote.
_MAX_ITEM_HEAT_FLOOR: float = importance_heat_floor(MAX_IMPORTANCE) * salience_heat_floor_multiplier(
    emotional_salience=1.0,
    relationship_proximity=1.0,
    evidence_strength=1.0,
)
# Float-noise tolerance for the exact floor comparison (the decay pass and
# this check compute the identical expression, but keep a margin anyway).
_FLOOR_EPSILON: float = 1e-9
# SQL band oversample: fetch a few multiples of the cap so items inside the
# band that are still above their OWN floor (partially decayed) don't crowd
# out eligible ones. Pathological distributions may defer some eligible
# items to the next sleep run — the sweep is recurring, so that's fine.
_CANDIDATE_FETCH_FACTOR: int = 4


@dataclass(slots=True)
class DistillationResult:
    distilled: int = 0
    failed: int = 0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def item_heat_floor(item: MemoryItem) -> float:
    """The item's own heat floor — the exact clamp ``compute_heat`` applies
    to a non-superseded scored item.

    Mirrors ``compute_heat_for_item``'s field coercions and reuses the two
    real functions (``importance_heat_floor`` from heat_scoring,
    ``salience_heat_floor_multiplier`` from memory_salience) so the value
    is bit-identical to what the decay pass wrote for a fully-decayed item.
    """
    evidence_strength = getattr(item, "evidence_strength", None)
    floor = importance_heat_floor(float(item.importance or 3))
    if floor <= 0.0:
        return 0.0
    return floor * salience_heat_floor_multiplier(
        emotional_salience=float(getattr(item, "emotional_salience", 0.0) or 0.0),
        relationship_proximity=float(getattr(item, "relationship_proximity", 0.0) or 0.0),
        evidence_strength=0.8 if evidence_strength is None else float(evidence_strength),
    )


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
    item.tags_json = None
    item.distilled_at = now

    # Scrub tag junction rows too: get_all_tags reads memory_item_tags
    # directly (user filter only), so a leftover tag would keep revealing
    # the distilled topic that item retrieval correctly hides.
    db.execute(
        delete(MemoryItemTag).where(MemoryItemTag.item_id == item.id)
    )

    # Collect evidence ids BEFORE deleting anything — the profile cleanup
    # below matches UserProfileField/Evidence by these source ids.
    memory_evidence_ids = list(
        db.scalars(
            select(MemoryItemEvidence.id).where(
                MemoryItemEvidence.user_id == user_id,
                MemoryItemEvidence.memory_item_id == item.id,
            )
        ).all()
    )
    # A casual/transient item may already have gone through upsert_claim,
    # leaving an active MemoryClaim (+ its evidence) linked by
    # memory_item_id — the original fact/preference. Distillation must
    # reduce the item to ONLY the new tendency; leaving the old claim would
    # keep the source content active and exportable and let profile
    # reconciliation resurface it. Mirror forget_memory's claim cleanup.
    # (The new tendency claim is linked with memory_item_id=None, so it is
    # never caught here.)
    linked_claim_ids = list(
        db.scalars(
            select(MemoryClaim.id).where(MemoryClaim.memory_item_id == item.id)
        ).all()
    )
    claim_evidence_ids = (
        list(
            db.scalars(
                select(MemoryClaimEvidence.id).where(
                    MemoryClaimEvidence.claim_id.in_(linked_claim_ids)
                )
            ).all()
        )
        if linked_claim_ids
        else []
    )

    # Profile reconciliation may have already derived active UserProfileField
    # rows from this item's claim/evidence; those keep surfacing the exact
    # fact in the profile block and vault exports after the item is gutted.
    # Scrub them (mirroring forget_memory) BEFORE deleting the evidence/claims
    # they reference.
    from anima_server.services.agent.forgetting import _delete_profile_fields_for_forget

    _delete_profile_fields_for_forget(
        db,
        user_id=user_id,
        source_memory_ids=[item.id],
        source_evidence_ids=memory_evidence_ids,
        source_claim_evidence_ids=claim_evidence_ids,
        runtime_contexts=[],
    )

    evidence_deleted = db.execute(
        delete(MemoryItemEvidence).where(
            MemoryItemEvidence.user_id == user_id,
            MemoryItemEvidence.memory_item_id == item.id,
        )
    ).rowcount or 0

    if linked_claim_ids:
        db.execute(
            delete(MemoryClaimEvidence).where(
                MemoryClaimEvidence.claim_id.in_(linked_claim_ids)
            )
        )
        db.execute(delete(MemoryClaim).where(MemoryClaim.id.in_(linked_claim_ids)))

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
    """Sweep casual/transient/emotional_pattern items that have fully
    decayed to their own salience floor into tendency claims (PRD IL5 —
    see the eligibility comment at the top of this module for why the
    trigger is floor-equality, not "below the visibility floor").

    Called from the F7 heat-decay sleep task (``sleep_agent._task_heat_decay``)
    AFTER heat has already been recomputed and committed for this sweep —
    the floor comparison is only meaningful against heat values
    ``decay_all_heat`` has actually written, and a distillation failure
    must never roll back heat-decay's own updates.
    Per-item transactional isolation mirrors IL4's crystallization loop
    (``latent_traces.crystallize_due_traces``): each item commits or rolls
    back on its own, so one bad item never aborts the rest of the sweep.
    Idempotent: already-distilled (``distilled_at`` set), superseded, and
    exempt-class items are excluded by the query itself.
    """
    ref_now = now or datetime.now(UTC)
    result = DistillationResult()
    cap = max(0, max_per_run)

    candidates = db.scalars(
        select(MemoryItem)
        .where(
            MemoryItem.user_id == user_id,
            MemoryItem.superseded_by.is_(None),
            MemoryItem.distilled_at.is_(None),
            MemoryItem.memory_class.in_(DISTILL_MEMORY_CLASSES),
            # SQL band pre-filter (cheap). heat == 0.0 means "never
            # scored" (visible by convention, see
            # heat_scoring.HEAT_SCORED_EPSILON) — only genuinely scored
            # items distill, so the sweep is safe even if ever invoked
            # outside the post-decay context. The ceiling is the maximum
            # floor any schema-valid item can have; the exact per-item
            # check below does the real work.
            MemoryItem.heat > 0.0,
            MemoryItem.heat <= _MAX_ITEM_HEAT_FLOOR + _FLOOR_EPSILON,
        )
        .order_by(MemoryItem.heat.asc(), MemoryItem.id.asc())
        .limit(cap * _CANDIDATE_FETCH_FACTOR)
    ).all()

    # Exact per-item check: "fully decayed" means heat has landed on the
    # item's OWN floor (compute_heat clamps there once the recency/access
    # contribution is gone).
    due = [
        item
        for item in candidates
        if item.heat <= item_heat_floor(item) + _FLOOR_EPSILON
    ][:cap]

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
