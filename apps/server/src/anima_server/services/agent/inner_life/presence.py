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
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from datetime import tzinfo as _tzinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.db.helpers import session_scope
from anima_server.models.runtime import RuntimeThread
from anima_server.models.runtime_consciousness import AffectStateRow
from anima_server.services.agent.inner_life.affect import (
    AffectConfig,
    AffectState,
    relax,
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


def _distinct_affect_user_ids(db: Session) -> list[int]:
    return [
        int(user_id)
        for user_id in db.scalars(select(AffectStateRow.user_id).distinct()).all()
    ]


def _active_user_ids(
    db: Session, *, now_utc: datetime, active_window_seconds: int
) -> set[int]:
    cutoff = now_utc - timedelta(seconds=active_window_seconds)
    rows = db.scalars(
        select(RuntimeThread.user_id).where(
            RuntimeThread.status == "active",
            RuntimeThread.last_message_at.isnot(None),
            RuntimeThread.last_message_at >= cutoff,
        )
    ).all()
    return {int(user_id) for user_id in rows}


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
            elapsed_hours = max(
                0.0,
                (local_now.astimezone(UTC) - state.updated_at).total_seconds()
                / 3600.0,
            )
            relaxed = relax(to_local_view(state, local_now.tzinfo), local_now, config)
            relaxed = to_utc_view(relaxed)
            updated = update_allostatic_shift(relaxed, elapsed_hours, config)
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
) -> PresenceTickResult:
    """Relax and accumulate allostatic load for every idle user.

    Enumerates distinct `user_id`s from `affect_state` (users with no
    affect row have nothing to tick). A user with an active `RuntimeThread`
    whose `last_message_at` falls within `active_window_seconds` (default
    `settings.presence_active_window_seconds`) is skipped entirely — not
    just lock-avoided, skipped by design, since consolidation owns their
    affect movement while a turn is live.

    `now` is expected to be timezone-aware local time (machine-local IS
    user-local for this desktop deployment); defaults to
    `datetime.now().astimezone()` when omitted.
    """
    local_now = now if now is not None else datetime.now().astimezone()
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

    return PresenceTickResult(users_ticked=ticked, users_skipped_active=skipped)
