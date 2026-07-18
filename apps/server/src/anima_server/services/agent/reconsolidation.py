"""IL6 — Recall reconsolidation (F2 extension).

When a memory item is actually rendered into the model's context (not
merely scored), retrieval stops being read-only: the recalled trace is
nudged toward the present — emotional salience drifts toward the current
turn's affect, and the stability class may move up the durability ladder
(recall is evidence a memory endures). Every applied nudge is bounded and
provenance-logged so the original extracted values are always exactly
reconstructable.

Zero LLM calls anywhere in this module (PRD §5 Architecture Rules) — every
function here is plain arithmetic over already-persisted salience fields.

House style mirrors ``distillation.py``: pure math (``reconsolidate_salience``,
``affect_magnitude``) with DB writes confined to the edge functions
(``apply_reconsolidation``, ``original_salience_from_log``).

Design decisions (documented up front since they're not fully pinned down
by the PRD prose):

- **Recency-of-relevance refresh is NOT re-implemented here.** F2 already
  bumps ``last_referenced_at``/``reference_count`` for every scored item via
  ``memory_store.touch_memory_items`` -> ``MemoryAccessLog`` ->
  ``access_sync.sync_access_metadata`` — a strict superset of "context
  included" (it fires on scoring, IL6 fires only on inclusion). Wiring
  ``apply_reconsolidation`` to also bump those fields would double-count
  the same access on every context-included item (access_sync and
  retrieval_feedback sync run back-to-back on the same items in
  ``soul_writer.py``). So the "recency refresh" bullet is satisfied
  structurally by the existing F2 mechanism; this module only owns the
  emotional_salience nudge and the stability upgrade.
- **The lifetime drift cap (Σ|Δ| ≤ 0.3) covers only the emotional_salience
  nudge.** It is the sole quantity that is genuinely "drift" in a bounded
  numeric sense. Stability-class upgrades are excluded from the same
  budget: they are monotonic (a item can only ever climb
  temporary -> evolving -> stable once, ever), driven by "recall happened"
  rather than by affect magnitude, and are already self-limiting (the
  ladder has exactly 2 possible upgrades total). Folding them into the
  same numeric budget would let an unrelated affect-driven cap block a
  structurally different signal.
- **Identity exemption covers exactly what the PRD names**: no
  emotional_salience nudge, no drift accrual from affect. Stability
  upgrades are NOT named as exempt, so they still apply (moot in practice,
  since identity items are created stable already).
- **current_affect_magnitude source**: the IL1 affect vector's
  valence/arousal (``inner_life/store.get_affect_state``), not the
  per-turn detected-emotion signal. It is already persisted at sync time
  with no new plumbing through the turn pipeline, and ``sync_retrieval_feedback``
  runs asynchronously from the turn that produced the feedback rows, so a
  "per-turn" emotion value would need to be threaded through the runtime
  feedback log — extra surface for no benefit, since IL1 affect already
  reflects the user's current emotional footing. See ``affect_magnitude``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models import MemoryItem, ReconsolidationLog
from anima_server.services.agent.memory_salience import (
    _STABILITY_STRENGTH,
    MEMORY_CLASS_IDENTITY,
    STABILITY_STABLE,
)

DEFAULT_ETA = 0.05
DEFAULT_DREAM_ETA = 0.02
DEFAULT_LIFETIME_DRIFT_CAP = 0.3

FIELD_EMOTIONAL_SALIENCE = "emotional_salience"
FIELD_STABILITY_CLASS = "stability_class"

_STABILITY_RANK_TO_CLASS = {rank: cls for cls, rank in _STABILITY_STRENGTH.items()}
_MAX_STABILITY_RANK = max(_STABILITY_STRENGTH.values())
_STABLE_RANK = _STABILITY_STRENGTH[STABILITY_STABLE]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _stability_rank(stability_class: str | None) -> int:
    return _STABILITY_STRENGTH.get(stability_class or STABILITY_STABLE, _STABLE_RANK)


def _upgrade_stability(stability_class: str | None) -> tuple[str, bool]:
    """One rung up the ladder (temporary -> evolving -> stable); a no-op
    (unchanged, not upgraded) once already at the top rung. Never
    downgrades — there is no code path here that can move rank down."""
    current = stability_class or STABILITY_STABLE
    rank = _stability_rank(current)
    if rank >= _MAX_STABILITY_RANK:
        return current, False
    return _STABILITY_RANK_TO_CLASS[rank + 1], True


@dataclass(frozen=True, slots=True)
class ReconsolidationState:
    """The subset of MemoryItem salience this module reads/writes."""

    emotional_salience: float
    stability_class: str


@dataclass(frozen=True, slots=True)
class ReconsolidationResult:
    """New salience values plus the per-component deltas actually applied
    (after drift-cap clamping) — everything ``apply_reconsolidation`` needs
    to decide what to write and log, and everything tests need to assert
    on."""

    emotional_salience: float
    emotional_salience_delta: float
    stability_class: str
    stability_upgraded: bool
    lifetime_drift_total: float
    drift_capped: bool


def reconsolidate_salience(
    state: ReconsolidationState,
    current_affect_magnitude: float | None,
    eta: float,
    lifetime_drift_so_far: float,
    *,
    is_identity: bool,
    drift_cap: float = DEFAULT_LIFETIME_DRIFT_CAP,
) -> ReconsolidationResult:
    """Pure IL6 nudge math (PRD "IL6 — Recall Reconsolidation").

    - ``emotional_salience += eta * (current_affect_magnitude - emotional_salience)``,
      clamped to [0, 1], further capped so the cumulative absolute drift
      this item has ever accrued from reconsolidation never exceeds
      ``drift_cap``. Skipped entirely (zero delta) when ``is_identity`` or
      when no affect signal is available (``current_affect_magnitude is
      None`` — never fabricate one) or when the cap is already exhausted.
    - ``stability_class`` may move up exactly one rung
      (temporary -> evolving -> stable), independent of affect
      availability and of the drift cap (see module docstring for why).
    """
    lifetime_drift_so_far = max(0.0, min(lifetime_drift_so_far, drift_cap))
    remaining_budget = max(0.0, drift_cap - lifetime_drift_so_far)

    emotional_salience = state.emotional_salience
    emotional_salience_delta = 0.0
    drift_capped = False

    if not is_identity and current_affect_magnitude is not None and remaining_budget > 0.0:
        raw_delta = eta * (current_affect_magnitude - state.emotional_salience)
        if abs(raw_delta) > remaining_budget:
            capped_delta = remaining_budget if raw_delta > 0 else -remaining_budget
            drift_capped = True
        else:
            capped_delta = raw_delta
        new_emotional = _clamp01(state.emotional_salience + capped_delta)
        emotional_salience_delta = new_emotional - state.emotional_salience
        emotional_salience = new_emotional
    elif not is_identity and current_affect_magnitude is not None:
        # Budget already exhausted before this call — a no-op, but still
        # "capped" in the sense the caller may want to report.
        drift_capped = True

    lifetime_drift_total = min(
        drift_cap, lifetime_drift_so_far + abs(emotional_salience_delta)
    )

    # Identity items get recency/confidence refresh ONLY (PRD IL6) — no
    # affect nudge (handled above) and no stability change. An item can
    # reach here as memory_class="identity" with a non-stable stability
    # class via LLM-set salience payloads, so the exemption must gate the
    # stability upgrade too, not just the affect nudge.
    if is_identity:
        new_stability_class, stability_upgraded = state.stability_class, False
    else:
        new_stability_class, stability_upgraded = _upgrade_stability(state.stability_class)

    return ReconsolidationResult(
        emotional_salience=emotional_salience,
        emotional_salience_delta=emotional_salience_delta,
        stability_class=new_stability_class,
        stability_upgraded=stability_upgraded,
        lifetime_drift_total=lifetime_drift_total,
        drift_capped=drift_capped,
    )


def affect_magnitude(valence: float, arousal: float) -> float:
    """Turn-affect intensity in [0, 1] from the IL1 vector.

    Combines valence magnitude (``|valence|``, bounded [-1, 1] -> [0, 1])
    and arousal (already [0, 1]) as their mean — a simple, symmetric
    "how far from neutral, how activated" reading of the persisted IL1
    state. Deterministic function of already-computed state; no new
    signal source, no LLM.
    """
    return _clamp01((abs(valence) + arousal) / 2.0)


def resolve_current_affect_magnitude(
    runtime_db: Session | None,
    *,
    user_id: int,
) -> float | None:
    """The turn's affect magnitude for reconsolidation, or None when no
    affect signal is available for this user yet (never fabricate one —
    the caller must then skip the emotional nudge entirely).

    "Available" means an ``AffectStateRow`` has actually been persisted for
    this user (i.e. at least one real IL1 update has happened) — a
    freshly-seeded default (``get_affect_state`` on a first read) is a
    config baseline, not a real signal, and would otherwise silently nudge
    every new user's memories toward an arbitrary constant.
    """
    if runtime_db is None:
        return None

    from anima_server.models.runtime_consciousness import AffectStateRow
    from anima_server.services.agent.inner_life.store import get_affect_state

    row = runtime_db.scalar(
        select(AffectStateRow).where(AffectStateRow.user_id == user_id)
    )
    if row is None:
        return None

    state = get_affect_state(runtime_db, user_id=user_id)
    return affect_magnitude(state.valence, state.arousal)


def apply_reconsolidation(
    soul_db: Session,
    item: MemoryItem,
    *,
    current_affect_magnitude: float | None,
    eta: float = DEFAULT_ETA,
    drift_cap: float = DEFAULT_LIFETIME_DRIFT_CAP,
    now: datetime | None = None,
) -> ReconsolidationResult | None:
    """Edge function: apply one reconsolidation touch to ``item`` and
    commit-ready-write the result, logging exactly the fields that
    actually changed.

    Returns ``None`` (no-op, nothing written) for superseded items and IL5
    distilled tombstones — the same "active item" guard family IL5 needed
    at every query site touching MemoryItem, defense-in-depth here since
    callers (e.g. the feedback-sync loop) should already be filtering
    these out before calling this.

    Per-item DB work is exactly: 0-2 provenance inserts plus the
    already-loaded ``item``'s attribute writes — no additional per-item
    SELECTs, so no N+1 regardless of how many items a sync cycle touches.
    """
    if item.superseded_by is not None or item.distilled_at is not None:
        return None

    ref_now = now or datetime.now(UTC)
    is_identity = (item.memory_class or "").strip().lower() == MEMORY_CLASS_IDENTITY

    state = ReconsolidationState(
        emotional_salience=float(item.emotional_salience or 0.0),
        stability_class=item.stability_class or STABILITY_STABLE,
    )
    lifetime_drift_so_far = float(getattr(item, "reconsolidation_drift", 0.0) or 0.0)

    result = reconsolidate_salience(
        state,
        current_affect_magnitude,
        eta,
        lifetime_drift_so_far,
        is_identity=is_identity,
        drift_cap=drift_cap,
    )

    if result.emotional_salience_delta != 0.0:
        soul_db.add(
            ReconsolidationLog(
                user_id=item.user_id,
                memory_item_id=item.id,
                applied_at=ref_now,
                field=FIELD_EMOTIONAL_SALIENCE,
                old_value=state.emotional_salience,
                new_value=result.emotional_salience,
                eta=eta,
            )
        )
        item.emotional_salience = result.emotional_salience
        item.reconsolidation_drift = result.lifetime_drift_total

    if result.stability_upgraded:
        soul_db.add(
            ReconsolidationLog(
                user_id=item.user_id,
                memory_item_id=item.id,
                applied_at=ref_now,
                field=FIELD_STABILITY_CLASS,
                old_value=float(_stability_rank(state.stability_class)),
                new_value=float(_stability_rank(result.stability_class)),
                eta=eta,
            )
        )
        item.stability_class = result.stability_class

    if result.emotional_salience_delta != 0.0 or result.stability_upgraded:
        soul_db.flush()

    return result


def original_salience_from_log(
    db: Session,
    *,
    item_id: int,
) -> dict[str, float] | None:
    """Reconstruct the pre-reconsolidation salience fields for an item from
    its provenance log — WITHOUT touching current MemoryItem state.

    Exact by construction: rather than replaying/undoing each delta (which
    would accumulate floating-point error across N applications), this
    reads the OLDEST logged ``old_value`` per field. The very first
    reconsolidation event's pre-nudge value IS the original extracted
    value — nothing wrote that field before it — so no arithmetic reversal
    is needed for exactness.

    Returns ``None`` if the item has never been reconsolidated (nothing to
    reconstruct — current MemoryItem values already ARE the originals).
    Returned ``"stability_class"`` value is the ``_STABILITY_STRENGTH``
    rank (numeric, matching the log column), not the class string.
    """
    rows = list(
        db.scalars(
            select(ReconsolidationLog)
            .where(ReconsolidationLog.memory_item_id == item_id)
            .order_by(
                ReconsolidationLog.applied_at.asc(), ReconsolidationLog.id.asc()
            )
        ).all()
    )
    if not rows:
        return None

    originals: dict[str, float] = {}
    for row in rows:
        originals.setdefault(row.field, row.old_value)
    return originals
