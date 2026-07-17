"""Offline catch-up (IL2): closed-form gap application, once, at startup.

IL1's relaxation is an exact closed-form solution in Δt (and the allostatic
update is applied piecewise-exactly around its single threshold crossing —
see `presence.apply_idle_gap`), so a restart after any gap — five minutes
or three weeks — applies the entire gap in O(1) per user: no per-minute
tick replay. This module is the only caller that ever passes a large Δt
into the gap application; the tick loop (`presence.py`) always sees small
ones.

Catch-up never runs a dream pass. If the gap contained at least one
eligible night window (>= 4h of idle time inside any local 00:00-06:00
span), it sets a `dream_deferred` marker on the audit row - a data flag,
nothing else - for IL-007 to consume later. At most one marker is ever set
per catch-up, regardless of how many nights the gap actually spanned.

Zero LLM calls; no behavioral output. Pure arithmetic plus a handful of
statements per user: the state SELECT, the save path's SELECT + UPDATE,
and the audit-row INSERT (linear in users, asserted by test).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from datetime import tzinfo as _tzinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.db.helpers import session_scope
from anima_server.models.runtime_consciousness import AffectStateRow, PresenceCatchup
from anima_server.services.agent.inner_life.affect import AffectConfig
from anima_server.services.agent.inner_life.presence import (
    apply_idle_gap,
    resolve_local_now,
)
from anima_server.services.agent.inner_life.store import (
    get_affect_config,
    get_affect_state,
    save_affect_state,
)

logger = logging.getLogger(__name__)

_CATCHUP_COMPONENTS = "affect,allostatic"

# A night window is any local 00:00-06:00 span; it is "eligible" for a
# deferred dream once at least this many idle hours fall inside it.
_NIGHT_WINDOW_HOURS = 6.0
_NIGHT_ELIGIBLE_IDLE_HOURS = 4.0


@dataclass(frozen=True, slots=True)
class CatchupResult:
    """One user's applied catch-up."""

    user_id: int
    gap_seconds: float
    dream_deferred: bool


def _night_overlap_hours(day: date, start: datetime, end: datetime) -> float:
    """Real idle hours of `[start, end)` inside `day`'s local 00:00-06:00 span.

    The window boundaries are local-calendar wall times (intra-zone
    wall-clock addition is exactly the "any local 00:00-06:00 span"
    semantics), but the overlap arithmetic is done on UTC instants:
    CPython's intra-zone rule makes subtraction/comparison of two aware
    datetimes sharing a tzinfo *wall-clock*, which would miscount real
    hours across a DST transition (a spring-forward night window holds
    only 5 real hours).
    """
    night_start = datetime.combine(day, time(hour=0), tzinfo=start.tzinfo)
    night_end = night_start + timedelta(hours=_NIGHT_WINDOW_HOURS)
    overlap_start = max(start.astimezone(UTC), night_start.astimezone(UTC))
    overlap_end = min(end.astimezone(UTC), night_end.astimezone(UTC))
    if overlap_end <= overlap_start:
        return 0.0
    return (overlap_end - overlap_start).total_seconds() / 3600.0


def has_eligible_night_window(start: datetime, end: datetime) -> bool:
    """Whether `[start, end)` (both local-aware) covers >= 4h of any night.

    Walks the calendar days touched by the gap — bounded by the gap's
    length in days, not its length in minutes, so a 21-day gap is ~21
    arithmetic checks, not 30,240 tick replays. Nearly every gap that
    spans more than a day or two contains a full night entirely, so this
    loop almost always exits on its first or second iteration in practice.
    """
    if end.astimezone(UTC) <= start.astimezone(UTC):
        return False
    day = start.date()
    last_day = end.date()
    while day <= last_day:
        if _night_overlap_hours(day, start, end) >= _NIGHT_ELIGIBLE_IDLE_HOURS:
            return True
        day += timedelta(days=1)
    return False


def _distinct_affect_user_ids(db: Session) -> list[int]:
    return [
        int(user_id)
        for user_id in db.scalars(select(AffectStateRow.user_id).distinct()).all()
    ]


def _catchup_one_user(
    runtime_db_factory: Callable[..., Session],
    *,
    user_id: int,
    local_now: datetime,
    now_utc: datetime,
    config: AffectConfig,
    min_gap_seconds: int,
) -> CatchupResult | None:
    try:
        with runtime_db_factory() as db:
            state = get_affect_state(
                db, user_id=user_id, config=config, for_update=True
            )
            gap_seconds = (now_utc - state.updated_at).total_seconds()
            if gap_seconds < min_gap_seconds:
                return None

            local_start = state.updated_at.astimezone(local_now.tzinfo)
            dream_deferred = has_eligible_night_window(local_start, local_now)

            updated = apply_idle_gap(state, local_now, config)
            save_affect_state(db, user_id=user_id, state=updated)

            db.add(
                PresenceCatchup(
                    user_id=user_id,
                    gap_seconds=gap_seconds,
                    components=_CATCHUP_COMPONENTS,
                    dream_deferred=dream_deferred,
                )
            )
            db.commit()
        return CatchupResult(
            user_id=user_id, gap_seconds=gap_seconds, dream_deferred=dream_deferred
        )
    except Exception:
        logger.warning("Offline catch-up failed for user %s", user_id, exc_info=True)
        return None


def apply_offline_catchup(
    runtime_db_factory: Callable[..., Session],
    *,
    now: datetime | None = None,
    config: AffectConfig | None = None,
    min_gap_seconds: int | None = None,
    tz: _tzinfo | None = None,
) -> list[CatchupResult]:
    """Apply the entire offline gap for every user with affect state, once.

    Users whose gap since `updated_at` is below `min_gap_seconds` are
    skipped silently: no write, no audit row. Meant to be called once at
    startup, before the presence-tick loop starts. `min_gap_seconds` is a
    test seam; it defaults from `settings.presence_catchup_min_gap_seconds`.
    `tz` is a test seam for the local zone; it defaults from
    `presence.system_zoneinfo()` (or `now`'s own zone when `now` is aware).
    """
    local_now = resolve_local_now(now, tz)
    now_utc = local_now.astimezone(UTC)
    resolved_config = config or get_affect_config()
    resolved_min_gap = (
        min_gap_seconds
        if min_gap_seconds is not None
        else settings.presence_catchup_min_gap_seconds
    )

    with session_scope(runtime_db_factory) as db:
        user_ids = _distinct_affect_user_ids(db)

    results: list[CatchupResult] = []
    for user_id in user_ids:
        result = _catchup_one_user(
            runtime_db_factory,
            user_id=user_id,
            local_now=local_now,
            now_utc=now_utc,
            config=resolved_config,
            min_gap_seconds=resolved_min_gap,
        )
        if result is not None:
            results.append(result)
    return results
