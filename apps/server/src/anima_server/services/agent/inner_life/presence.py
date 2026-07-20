"""Presence tick loop (IL2): background composition of IL1 affect dynamics.

The tick is the edge that turns the pure `(state, event, Δt) → state`
functions in `affect.py` into a recurring side effect: once per cadence it
relaxes every idle user's affect toward baseline and accumulates allostatic
load, one short session/transaction per user (mirroring the write pattern
`consolidation._apply_affect_turn_deltas_best_effort` established for IL1).
Users with an active turn in flight are skipped entirely — consolidation
owns their affect movement while a session is live, so the tick never
contends for that row.

No DB writes happen here beyond the per-user affect read/relax/save; no LLM
calls anywhere (this module and `catchup.py` are pure arithmetic + DB).
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from datetime import tzinfo as _tzinfo
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.db.helpers import session_scope
from anima_server.models.runtime import RuntimeRun, RuntimeThread
from anima_server.models.runtime_consciousness import AffectStateRow
from anima_server.services.agent.inner_life.affect import (
    AffectConfig,
    AffectState,
    _advance,
    arousal_threshold_crossing_time,
    relax,
    relax_with_shift_dynamics,
    update_allostatic_shift,
)
from anima_server.services.agent.inner_life.store import (
    get_affect_config,
    get_affect_state,
    save_affect_state,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PresenceTickResult:
    """Summary of one presence-tick sweep."""

    users_ticked: int = 0
    users_skipped_active: int = 0


# --- Local-time seam ---------------------------------------------------
#
# `affect.relax` reads local hour-of-day directly off the datetimes it is
# given (`.hour` via `_local_hour`) — it does not do timezone conversion
# itself, by design (that's a side-effect concern). This is a desktop
# deployment: the server runs on the user's own machine, so machine-local
# time IS the user's local time for circadian purposes. The stored
# `updated_at` is always UTC (see `store.py`), so pairing a local-aware
# `now` with it directly would silently mix two reference frames — the
# "then" hour would read as UTC while the "now" hour reads as local. These
# two helpers close that gap at the wiring edge, without changing
# `affect.py`'s semantics: re-view the stored instant in the same zone as
# `now` before calling `relax`, then convert the result back to UTC before
# it is persisted.


def to_local_view(state: AffectState, tz: _tzinfo | None) -> AffectState:
    """Re-view `state.updated_at` in `tz`, without changing the instant."""
    return replace(state, updated_at=state.updated_at.astimezone(tz))


def to_utc_view(state: AffectState) -> AffectState:
    """Convert `state.updated_at` back to UTC before it is persisted."""
    return replace(state, updated_at=state.updated_at.astimezone(UTC))


@lru_cache(maxsize=1)
def system_zoneinfo() -> _tzinfo:
    """Resolve the machine's real IANA timezone (DST-aware).

    `datetime.now().astimezone()` yields a FIXED-offset tzinfo frozen at
    call time; reusing it across a gap that spans a DST transition
    misplaces local midnight by the DST delta, corrupting night-window
    eligibility and circadian phase. Resolution order: the TZ env var (if
    it names a valid zone), then the /etc/localtime symlink target, then —
    with a logged warning — the current fixed offset as a last resort.

    Cached for the process lifetime (the tick calls this every cadence;
    without caching the fixed-offset fallback would warn every 60 s).
    Tests exercising resolution should call `system_zoneinfo.cache_clear()`.
    """
    tz_name = os.environ.get("TZ")
    if tz_name:
        try:
            return ZoneInfo(tz_name.strip())
        except ZoneInfoNotFoundError:
            logger.warning(
                "TZ=%r is not a valid IANA zone; trying /etc/localtime", tz_name
            )
    try:
        target = str(Path("/etc/localtime").resolve())
        marker = "zoneinfo/"
        idx = target.rfind(marker)
        if idx != -1:
            return ZoneInfo(target[idx + len(marker):])
    except Exception:
        pass
    try:
        from dateutil import tz

        tzlocal = tz.tzlocal()
        logger.warning(
            "Could not resolve an IANA timezone; using OS-local rules from "
            "dateutil.tz.tzlocal()."
        )
        return tzlocal
    except Exception:
        logger.warning(
            "Could not resolve an IANA timezone; falling back to the current fixed "
            "UTC offset (DST transitions inside long gaps will be misplaced)"
        )
    return datetime.now().astimezone().tzinfo or UTC


def resolve_local_now(now: datetime | None, tz: _tzinfo | None) -> datetime:
    """Resolve the timezone-aware local `now` for a tick or catch-up.

    `tz` wins when given (tests inject e.g. ZoneInfo("America/New_York"));
    otherwise an aware `now` keeps its own zone; otherwise the real system
    zone from `system_zoneinfo()`.
    """
    if now is not None:
        resolved_tz = tz if tz is not None else (now.tzinfo or system_zoneinfo())
        return now.astimezone(resolved_tz)
    return datetime.now(tz if tz is not None else system_zoneinfo())


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _shift_of_load(load: float, config: AffectConfig) -> float:
    """The shift `update_allostatic_shift` pins while load exceeds sustained."""
    span = config.high_arousal_hours_cap - config.allostatic_sustained_hours
    excess = load - config.allostatic_sustained_hours
    return config.allostatic_max_shift * _clamp(excess / span, 0.0, 1.0)


def _load_crossing_offsets(
    load0: float, rate: float, duration: float, config: AffectConfig
) -> list[float]:
    """Offsets in (0, duration) where the shift law changes regime.

    Load moves linearly within a phase (`load0 + rate * t`), so crossings
    of the sustained threshold and the cap are plain algebra.
    """
    offsets = []
    for level in (config.allostatic_sustained_hours, config.high_arousal_hours_cap):
        if (rate > 0 and load0 < level) or (rate < 0 and load0 > level):
            t = (level - load0) / rate
            if 0.0 < t < duration:
                offsets.append(t)
    return sorted(offsets)


def _segment_shift_law(
    load0: float, rate: float, shift0: float, config: AffectConfig
) -> tuple[float, float, float | None]:
    """(shift_initial, shift_slope, shift_decay_tau) for one gap segment.

    Mirrors `update_allostatic_shift`'s regimes in continuous form: load
    pinned at the cap -> constant max shift; load above sustained -> shift
    is a linear function of the linearly-moving load (snapping to f(load)
    regardless of the stored shift, exactly as the discrete update does);
    otherwise -> exponential decay of the carried shift toward zero.
    """
    sustained = config.allostatic_sustained_hours
    span = config.high_arousal_hours_cap - config.allostatic_sustained_hours
    if rate > 0 and load0 >= config.high_arousal_hours_cap:
        return (config.allostatic_max_shift, 0.0, None)
    interior_above = load0 > sustained or (load0 == sustained and rate > 0)
    if interior_above:
        return (_shift_of_load(load0, config), config.allostatic_max_shift / span * rate, None)
    return (shift0, 0.0, config.allostatic_decay_tau_hours)


def apply_idle_gap(
    state: AffectState,
    local_now: datetime,
    config: AffectConfig,
) -> AffectState:
    """Apply one idle gap in closed form: piecewise-exact relax + allostatic.

    The gap is segmented wherever the piecewise dynamics change law, and
    each segment is fully closed-form — O(1) in gap length throughout:

    - t* — arousal's single downward crossing of the allostatic threshold
      (`arousal_threshold_crossing_time`, bisection): accumulation before,
      drain after;
    - load crossings of the sustained threshold / cap within each phase —
      algebraic, since load is linear per phase (+1 h/h accumulating,
      -0.5 h/h draining).

    Within each segment the shift follows one law (`_segment_shift_law`)
    and `relax_with_shift_dynamics` absorbs it into the arousal solution
    as a closed-form particular term — so a multi-week gap in which the
    allostatic shift decays produces the same end arousal as tick
    composition, instead of freezing the shift at its stored value.
    `update_allostatic_shift` performs the per-segment load bookkeeping
    (branch selected by the segment's phase); the carried shift is then
    set to the law's own end value — identical to the update's result
    except at a segment ending exactly ON the sustained threshold, where
    the discrete update falls into the decay branch while the continuous
    law says f(sustained) = 0.

    t* is solved on the law-consistent trajectory (the solver composes the
    same accumulation-regime shift segments this function applies over
    [0, t*]), so the only divergence vs per-minute composition is the tick
    quantization documented at the acceptance level.

    `state` is the persisted (UTC-view) row; `local_now` must be aware and
    carry the zone circadian phase should be read in. Returns the UTC view
    ready for persistence.
    """
    tz = local_now.tzinfo
    local_state = to_local_view(state, tz)
    # Subtract in UTC: intra-zone aware subtraction is wall-clock by
    # CPython's rule and would mis-measure a gap spanning a DST transition.
    gap_hours = max(
        0.0, (local_now.astimezone(UTC) - state.updated_at).total_seconds() / 3600.0
    )
    if gap_hours <= 0.0:
        return to_utc_view(relax(local_state, local_now, config))

    t_star = min(arousal_threshold_crossing_time(local_state, config), gap_hours)

    # (end_offset, arousal_above) phases, then split at load-law changes.
    phases: list[tuple[float, bool]] = []
    if t_star > 0.0:
        phases.append((t_star, True))
    if gap_hours > t_star:
        phases.append((gap_hours, False))

    current = local_state
    start_moment = local_state.updated_at
    offset = 0.0
    load = state.high_arousal_hours
    shift = state.arousal_baseline_shift

    for phase_end, above in phases:
        rate = 1.0 if above else -config.allostatic_recovery_drain_rate
        crossings = _load_crossing_offsets(load, rate, phase_end - offset, config)
        for boundary in [*[offset + c for c in crossings], phase_end]:
            duration = boundary - offset
            if duration <= 0.0:
                continue
            initial, slope, decay_tau = _segment_shift_law(load, rate, shift, config)
            moment = (
                local_now
                if boundary >= gap_hours
                else _advance(start_moment, boundary)
            )
            relaxed = relax_with_shift_dynamics(
                current,
                moment,
                config,
                shift_initial=initial,
                shift_slope=slope,
                shift_decay_tau_hours=decay_tau,
            )
            stepped = update_allostatic_shift(
                replace(relaxed, arousal=current.arousal if above else relaxed.arousal),
                duration,
                config,
            )
            load = stepped.high_arousal_hours
            if decay_tau is not None:
                shift = initial * math.exp(-duration / decay_tau)
            else:
                shift = _clamp(
                    initial + slope * duration, 0.0, config.allostatic_max_shift
                )
            current = replace(
                stepped, arousal=relaxed.arousal, arousal_baseline_shift=shift
            )
            offset = boundary

    return to_utc_view(current)


def _distinct_affect_user_ids(db: Session) -> list[int]:
    return [
        int(user_id)
        for user_id in db.scalars(select(AffectStateRow.user_id).distinct()).all()
    ]


def _active_user_ids(
    db: Session, *, now_utc: datetime, active_window_seconds: int
) -> set[int]:
    """Users the tick must skip: recent message activity OR a turn in flight.

    `last_message_at` alone misses turns running longer than the active
    window (it goes stale while the turn is still generating), so users
    with a RuntimeRun in status "running" are also counted — capped by
    `presence_run_stale_seconds` on `started_at` (the freshest timestamp
    the model carries for a live run), so a crashed run stuck in
    "running" cannot exclude a user from presence forever.
    """
    message_cutoff = now_utc - timedelta(seconds=active_window_seconds)
    recent = db.scalars(
        select(RuntimeThread.user_id).where(
            RuntimeThread.status == "active",
            RuntimeThread.last_message_at.isnot(None),
            RuntimeThread.last_message_at >= message_cutoff,
        )
    ).all()
    run_cutoff = now_utc - timedelta(seconds=settings.presence_run_stale_seconds)
    in_flight = db.scalars(
        select(RuntimeRun.user_id).where(
            RuntimeRun.status == "running",
            RuntimeRun.started_at >= run_cutoff,
        )
    ).all()
    return {int(user_id) for user_id in recent} | {int(user_id) for user_id in in_flight}


def _tick_one_user(
    runtime_db_factory: Callable[..., Session],
    *,
    user_id: int,
    local_now: datetime,
    config: AffectConfig,
) -> bool:
    """Relax + accumulate allostatic load for one user, in its own session.

    Returns whether the tick succeeded. Failures are logged and swallowed
    so one bad user never aborts the sweep (mirrors
    `eager_consolidation.inactivity_sweep`'s per-item error isolation).
    """
    try:
        with runtime_db_factory() as db:
            state = get_affect_state(
                db, user_id=user_id, config=config, for_update=True
            )
            updated = apply_idle_gap(state, local_now, config)
            save_affect_state(db, user_id=user_id, state=updated)
            db.commit()
        return True
    except Exception:
        logger.warning("Presence tick failed for user %s", user_id, exc_info=True)
        return False


def run_presence_tick(
    runtime_db_factory: Callable[..., Session],
    *,
    now: datetime | None = None,
    config: AffectConfig | None = None,
    active_window_seconds: int | None = None,
    tz: _tzinfo | None = None,
    soul_db_factory: Callable[..., Session] | None = None,
) -> PresenceTickResult:
    """Relax and accumulate allostatic load for every idle user.

    Enumerates distinct `user_id`s from `affect_state` (users with no
    affect row have nothing to tick). A user is skipped entirely — not
    just lock-avoided, skipped by design, since consolidation owns their
    affect movement while a turn is live — when they have an active
    `RuntimeThread` with `last_message_at` within `active_window_seconds`
    OR a `RuntimeRun` in flight (status "running", started within
    `settings.presence_run_stale_seconds` — the cap keeps a crashed run
    from excluding a user from presence forever).

    `now` must be timezone-aware when given; production omits it and gets
    the current instant in the machine's real IANA zone (machine-local IS
    user-local for this desktop deployment). `active_window_seconds` is a
    test seam; it defaults from `settings.presence_active_window_seconds`.
    `tz` is a test seam for the local zone; it defaults from
    `system_zoneinfo()` (or `now`'s own zone when `now` is aware).

    `soul_db_factory` opts a caller into also advancing IL3 drive
    accumulators (and firing an initiative if opted in and gated through)
    for the same idle set, via `inner_life.initiative.tick_initiative_for_user`
    — one call per idle user, isolated from the affect tick and from each
    other exactly like `_tick_one_user`. Left `None` (the default), this
    function's behavior is byte-for-byte what it was before IL3: existing
    callers and tests that don't pass it see no new DB access, no new
    tables touched, nothing. Production wiring (`main.py`) passes the real
    soul-store session factory; the initiative tick's own opt-in gate
    (`PresenceConfig.initiative_enabled`, off by default) is what actually
    prevents unwanted messages, not this parameter.
    """
    local_now = resolve_local_now(now, tz)
    now_utc = local_now.astimezone(UTC)
    resolved_config = config or get_affect_config()
    window = (
        active_window_seconds
        if active_window_seconds is not None
        else settings.presence_active_window_seconds
    )

    with session_scope(runtime_db_factory) as db:
        user_ids = _distinct_affect_user_ids(db)
        active_ids = _active_user_ids(
            db, now_utc=now_utc, active_window_seconds=window
        )

    ticked = 0
    skipped = 0
    for user_id in user_ids:
        if user_id in active_ids:
            skipped += 1
            continue
        if _tick_one_user(
            runtime_db_factory,
            user_id=user_id,
            local_now=local_now,
            config=resolved_config,
        ):
            ticked += 1
        if soul_db_factory is not None:
            from anima_server.services.agent.inner_life.initiative import (
                tick_initiative_for_user,
            )

            tick_initiative_for_user(
                soul_db_factory,
                runtime_db_factory,
                user_id=user_id,
                local_now=local_now,
            )

    return PresenceTickResult(users_ticked=ticked, users_skipped_active=skipped)
