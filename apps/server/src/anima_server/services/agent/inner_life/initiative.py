"""IL3 — Gate chain, firing decision, and the presence-tick edge wiring.

Pure part (no DB, no LLM, no clock reads): ``GateConfig``/``GateStates``/
``DriveRecord``/``DriveDecision`` and the functions that turn a
``DriveRecord`` + gate config + already-resolved ``now``/``closeness`` into a
fire-or-not decision — ``compute_gate_states``, ``dominant_drive``,
``should_fire``. All five gates from the PRD are represented:

1. ``PresenceConfig.initiative_enabled`` — off by default (opt-in).
2. Outside user-configured quiet hours.
3. Adaptive cooldown: 24h base, scaled down toward 8h as closeness rises,
   scaled up after unanswered initiatives.
4. Rate caps: <=1/day, <=3/week (counted from ``InitiativeLog`` at the edge).
5. Idle-only — NOT re-checked here. ``tick_initiative_for_user`` is only
   ever invoked by ``run_presence_tick`` for users already excluded from the
   tick's idle set, exactly like the IL1 affect tick — so this gate is
   satisfied by construction, not by a runtime check. ``GateStates.idle`` is
   still recorded (always ``True``) purely for provenance completeness.

Edge part: signal resolution (ForesightSignal / pattern MemoryItem / contact
cadence / topic diversity / the dream-residue stub), the closeness proxy,
persistence of ``DriveStateRow``, the ONE small-LLM generation call, the
``InitiativeLog`` provenance write, delivery, and the per-user presence-tick
sibling function itself. Zero LLM anywhere except
``generate_initiative_message`` (source-level test asserts this).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models import (
    DreamJournal,
    ForesightSignal,
    InitiativeLog,
    MemoryEpisode,
    MemoryItem,
)
from anima_server.models.runtime import RuntimeThread
from anima_server.models.runtime_consciousness import DriveStateRow
from anima_server.services.agent.foresight import FORESIGHT_ACTIVE_STATUSES
from anima_server.services.agent.inner_life.delivery import (
    InitiativeDelivery,
    PendingInitiativeDelivery,
)
from anima_server.services.agent.inner_life.drives import (
    DRIVE_DREAM_RESIDUE,
    DRIVE_NAMES,
    DRIVE_NOVELTY,
    DRIVE_PATTERN_INSIGHT,
    DRIVE_RELATIONAL,
    DRIVE_UNRESOLVED_THREAD,
    DriveConfig,
    DriveSignals,
    DriveState,
    advance_drives,
    reset_drive,
    signal_reset_drives,
)
from anima_server.services.data_crypto import DOMAIN_MEMORIES, df, ef
from anima_server.services.sessions import get_active_dek

logger = logging.getLogger(__name__)

# Pattern-synthesis storage constant (avoids importing pattern_synthesis.py's
# private helpers; this string is its public storage contract).
_PATTERN_CATEGORY = "pattern"

# The foresight statuses that still count as an OPEN, unresolved thread for
# IL3 — reuse foresight's own canonical set ({active, due, occurred}) so this
# never drifts from the lifecycle state machine (an item is promoted
# active -> due -> occurred before it finally goes stale/cancelled).
_OPEN_FORESIGHT_STATUSES: frozenset[str] = FORESIGHT_ACTIVE_STATUSES


def _open_in_horizon_foresight(user_id: int, today: date, horizon_days: int):
    """The single definition of "an open, in-horizon foresight thread" for
    IL3, ordered soonest-first. BOTH the ``unresolved_thread`` grow signal and
    ``gather_drive_material`` build from this so they can never disagree about
    which item is driving the pressure — the material spoken must be the same
    item that actually accumulated the drive (matching start_date presence and
    the horizon window, not just status)."""
    return (
        select(ForesightSignal)
        .where(
            ForesightSignal.user_id == user_id,
            ForesightSignal.status.in_(_OPEN_FORESIGHT_STATUSES),
            ForesightSignal.start_date.isnot(None),
            ForesightSignal.start_date <= today + timedelta(days=horizon_days),
        )
        .order_by(ForesightSignal.start_date.asc())
    )


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Pure: gate chain + firing decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateConfig:
    enabled: bool
    quiet_hours_start: int | None
    quiet_hours_end: int | None
    cooldown_base_hours: float = 24.0
    cooldown_min_hours: float = 8.0
    cooldown_backoff_factor: float = 1.5
    cooldown_max_hours: float = 168.0
    max_per_day: int = 1
    max_per_week: int = 3
    thetas: dict[str, float] | None = None
    # IL-013: per-loss ranking boost for above-theta drives that lost a
    # selection, and its hard cap. Ranking only — never qualification.
    starvation_boost_per_loss: float = 0.03
    starvation_boost_cap: float = 0.15

    def theta(self, drive: str) -> float:
        if self.thetas and drive in self.thetas:
            return self.thetas[drive]
        return 0.7


@dataclass(frozen=True, slots=True)
class GateStates:
    enabled: bool
    outside_quiet_hours: bool
    cooldown_elapsed: bool
    under_daily_cap: bool
    under_weekly_cap: bool
    idle: bool = True

    @property
    def all_pass(self) -> bool:
        return (
            self.enabled
            and self.outside_quiet_hours
            and self.cooldown_elapsed
            and self.under_daily_cap
            and self.under_weekly_cap
            and self.idle
        )

    def as_dict(self) -> dict[str, bool]:
        return {
            "enabled": self.enabled,
            "outside_quiet_hours": self.outside_quiet_hours,
            "cooldown_elapsed": self.cooldown_elapsed,
            "under_daily_cap": self.under_daily_cap,
            "under_weekly_cap": self.under_weekly_cap,
            "idle": self.idle,
        }


@dataclass(frozen=True, slots=True)
class DriveRecord:
    """Full per-user IL3 state the gate chain needs: the five pressures plus
    firing bookkeeping (``drives.DriveState`` covers only the pressures)."""

    pressures: DriveState
    last_fired_at: datetime | None = None
    last_user_turn_at: datetime | None = None
    unanswered_initiatives: int = 0
    # IL-013: per-drive count of selections this drive lost while above its
    # theta. Missing drives count as 0. None means "no bookkeeping" (older
    # callers/tests) and behaves exactly like an all-zero map.
    starvation_losses: Mapping[str, int] | None = None


@dataclass(frozen=True, slots=True)
class DriveDecision:
    drive: str
    pressure: float
    pressure_snapshot: dict[str, float]
    gate_states: dict[str, bool]
    # IL-013 provenance: the ranking boost each QUALIFYING drive carried into
    # this selection (only non-zero entries). Raw pressures above stay
    # untouched; `_fire` folds this under a dedicated "starvation" key in the
    # logged pressure_snapshot JSON so every decision remains explainable.
    starvation_snapshot: dict[str, float] = field(default_factory=dict)


def _in_quiet_hours(hour: int, start: int | None, end: int | None) -> bool:
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight


def effective_cooldown_hours(
    config: GateConfig, *, closeness: float, unanswered_initiatives: int
) -> float:
    """24h base scaled down toward the configured floor as closeness rises,
    scaled up after unanswered initiatives (PRD IL3 gate 3)."""
    closeness = _clamp01(closeness)
    base = config.cooldown_base_hours - (
        config.cooldown_base_hours - config.cooldown_min_hours
    ) * closeness
    base = max(config.cooldown_min_hours, base)
    backoff = config.cooldown_backoff_factor ** max(0, unanswered_initiatives)
    return min(config.cooldown_max_hours, base * backoff)


def starvation_boost(losses: int, config: GateConfig) -> float:
    """IL-013: the ranking boost a drive carries after ``losses`` lost
    selections — ``min(losses * per_loss, cap)``, never negative. The cap
    bounds how much fairness can override raw pressure: a pressure gap wider
    than the cap is never overcome, by design."""
    return min(max(0, losses) * config.starvation_boost_per_loss,
               config.starvation_boost_cap)


def dominant_drive(
    pressures: DriveState,
    config: GateConfig,
    starvation_losses: Mapping[str, int] | None = None,
) -> tuple[str, float] | None:
    """The winning drive among those at/above their own theta, or ``None``
    if no drive qualifies. Ties break by ``DRIVE_NAMES`` order.

    Qualification is by RAW pressure vs theta. Ranking adds each qualifying
    drive's starvation boost (IL-013), so a chronically outranked drive that
    keeps qualifying eventually wins — a pure argmax would let a perennially
    high-pressure drive win every fire forever (scheduler starvation; the
    surface-reset drives ``pattern_insight``/``dream_residue`` are the likely
    victims). The boost can never pull a sub-theta drive into candidacy.
    Returns the winner with its RAW pressure (provenance stays truthful)."""
    losses = starvation_losses or {}
    candidates = [
        (name, getattr(pressures, name))
        for name in DRIVE_NAMES
        if getattr(pressures, name) >= config.theta(name)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda pair: pair[1] + starvation_boost(losses.get(pair[0], 0), config),
    )


def compute_gate_states(
    record: DriveRecord,
    config: GateConfig,
    now: datetime,
    *,
    closeness: float,
    fires_today: int,
    fires_this_week: int,
) -> GateStates:
    """All five gates, evaluated independently so tests (and provenance) can
    see exactly which one blocked. ``now`` must already be a local-time-view
    datetime (see ``inner_life.presence.system_zoneinfo``/``resolve_local_now``)
    — quiet hours read ``now.hour`` directly, the same local-time discipline
    IL2 established."""
    cooldown_hours = effective_cooldown_hours(
        config,
        closeness=closeness,
        unanswered_initiatives=record.unanswered_initiatives,
    )
    if record.last_fired_at is None:
        cooldown_elapsed = True
    else:
        last_fired_utc = _as_utc(record.last_fired_at)
        now_utc = now.astimezone(UTC)
        elapsed_hours = (now_utc - last_fired_utc).total_seconds() / 3600.0
        cooldown_elapsed = elapsed_hours >= cooldown_hours

    return GateStates(
        enabled=config.enabled,
        outside_quiet_hours=not _in_quiet_hours(
            now.hour, config.quiet_hours_start, config.quiet_hours_end
        ),
        cooldown_elapsed=cooldown_elapsed,
        under_daily_cap=fires_today < config.max_per_day,
        under_weekly_cap=fires_this_week < config.max_per_week,
        idle=True,
    )


def should_fire(
    record: DriveRecord,
    config: GateConfig,
    now: datetime,
    closeness: float,
    *,
    fires_today: int,
    fires_this_week: int,
) -> DriveDecision | None:
    """The dominant drive if every gate passes and some drive is at/above
    its threshold; ``None`` otherwise. Provably cannot fire when
    ``config.enabled`` is False, regardless of pressure — ``GateStates.all_pass``
    ANDs every gate, and ``enabled`` is one of them."""
    gates = compute_gate_states(
        record,
        config,
        now,
        closeness=closeness,
        fires_today=fires_today,
        fires_this_week=fires_this_week,
    )
    dominant = dominant_drive(record.pressures, config, record.starvation_losses)
    if dominant is None or not gates.all_pass:
        return None
    drive, pressure = dominant
    losses = record.starvation_losses or {}
    snapshot = {
        name: boost
        for name in DRIVE_NAMES
        if getattr(record.pressures, name) >= config.theta(name)
        and (boost := starvation_boost(losses.get(name, 0), config)) > 0.0
    }
    return DriveDecision(
        drive=drive,
        pressure=pressure,
        pressure_snapshot=record.pressures.as_dict(),
        gate_states=gates.as_dict(),
        starvation_snapshot=snapshot,
    )


# ---------------------------------------------------------------------------
# Edge: config resolution
# ---------------------------------------------------------------------------


def get_drive_config() -> DriveConfig:
    return DriveConfig(
        growth_unresolved_thread=settings.initiative_growth_unresolved_thread,
        growth_pattern_insight=settings.initiative_growth_pattern_insight,
        growth_relational=settings.initiative_growth_relational,
        growth_novelty=settings.initiative_growth_novelty,
        growth_dream_residue=settings.initiative_growth_dream_residue,
        leak_tau_hours=settings.initiative_pressure_leak_tau_hours,
    )


def get_gate_config(presence_values: object) -> GateConfig:
    """Build a ``GateConfig`` from ``Settings`` + a ``PresenceConfigValues``
    (typed loosely to avoid a hard import cycle with ``presence_config.py``,
    which itself only needs the model, not this module)."""
    return GateConfig(
        # Both the master Presence switch AND the initiative opt-in must be on.
        # `PresenceConfig.enabled` is the top-level kill switch for ALL
        # proactive notices (see proactive.py), so a user who paused Presence
        # must never receive an initiative even with initiative_enabled left on.
        enabled=bool(getattr(presence_values, "enabled", True))
        and bool(getattr(presence_values, "initiative_enabled", False)),
        quiet_hours_start=getattr(presence_values, "quiet_hours_start", None),
        quiet_hours_end=getattr(presence_values, "quiet_hours_end", None),
        cooldown_base_hours=settings.initiative_cooldown_base_hours,
        cooldown_min_hours=settings.initiative_cooldown_min_hours,
        cooldown_backoff_factor=settings.initiative_cooldown_backoff_factor,
        cooldown_max_hours=settings.initiative_cooldown_max_hours,
        max_per_day=settings.initiative_max_per_day,
        max_per_week=settings.initiative_max_per_week,
        starvation_boost_per_loss=settings.initiative_starvation_boost_per_loss,
        starvation_boost_cap=settings.initiative_starvation_boost_cap,
        thetas={
            DRIVE_UNRESOLVED_THREAD: settings.initiative_theta_unresolved_thread,
            DRIVE_PATTERN_INSIGHT: settings.initiative_theta_pattern_insight,
            DRIVE_RELATIONAL: settings.initiative_theta_relational,
            DRIVE_NOVELTY: settings.initiative_theta_novelty,
            DRIVE_DREAM_RESIDUE: settings.initiative_theta_dream_residue,
        },
    )


# ---------------------------------------------------------------------------
# Edge: closeness proxy
# ---------------------------------------------------------------------------


def resolve_closeness_signal(
    runtime_db: Session | None, *, user_id: int, now: datetime
) -> float:
    """Documented PROXY for relationship closeness (PRD IL3 gate 3 names
    "closeness from the self-model human block"). No structured closeness
    scalar exists yet: the self-model ``human`` section is free-text prose
    (``self_model.py``), and ``conversation_policy.RelationshipPolicy.stage``
    is computed per-turn from rendered memory blocks that don't exist outside
    a live turn — unavailable to a background sweep with no turn context.

    Proxy: relationship age since first contact (the oldest ``RuntimeThread``
    for this user), saturating linearly to full closeness (1.0) at
    ``settings.initiative_closeness_full_days``. Superseded cleanly once the
    self-model carries a real structured closeness value. Measured against
    the CALLER's ``now`` (never the wall clock), like every other IL3 edge
    function, so it stays deterministic under an injected tick time.
    """
    if runtime_db is None:
        return 0.0
    first_contact = runtime_db.scalar(
        select(func.min(RuntimeThread.created_at)).where(RuntimeThread.user_id == user_id)
    )
    if first_contact is None:
        return 0.0
    age_days = (now.astimezone(UTC) - _as_utc(first_contact)).total_seconds() / 86400.0
    return _clamp01(age_days / settings.initiative_closeness_full_days)


# ---------------------------------------------------------------------------
# Edge: DriveStateRow persistence
# ---------------------------------------------------------------------------


def _get_or_seed_drive_row(
    runtime_db: Session, *, user_id: int, for_update: bool = False
) -> DriveStateRow:
    stmt = select(DriveStateRow).where(DriveStateRow.user_id == user_id)
    if for_update:
        stmt = stmt.with_for_update()
    row = runtime_db.scalar(stmt)
    if row is not None:
        return row

    row = DriveStateRow(user_id=user_id, updated_at=datetime.now(UTC))
    try:
        with runtime_db.begin_nested():
            runtime_db.add(row)
            runtime_db.flush()
    except IntegrityError:
        row = runtime_db.scalar(stmt)
        if row is None:
            raise
    return row


def _pressures_of(row: DriveStateRow) -> DriveState:
    return DriveState(
        unresolved_thread=row.unresolved_thread,
        pattern_insight=row.pattern_insight,
        relational=row.relational,
        novelty=row.novelty,
        dream_residue=row.dream_residue,
    )


def _apply_pressures(row: DriveStateRow, pressures: DriveState, *, now: datetime) -> None:
    row.unresolved_thread = pressures.unresolved_thread
    row.pattern_insight = pressures.pattern_insight
    row.relational = pressures.relational
    row.novelty = pressures.novelty
    row.dream_residue = pressures.dream_residue
    row.updated_at = now.astimezone(UTC)


# ---------------------------------------------------------------------------
# Edge: signal resolution
# ---------------------------------------------------------------------------


def _topics_of(episode: MemoryEpisode) -> frozenset[str]:
    raw = episode.topics_json or []
    return frozenset(
        str(topic).strip().casefold() for topic in raw if topic is not None and str(topic).strip()
    )


def _unsurfaced_pattern_query(
    user_id: int,
    pattern_marker: datetime | None,
    pattern_marker_id: int | None = None,
):
    """Active pattern-synthesis MemoryItems not yet voiced, OLDEST first.

    The single shared predicate for the grow signal (existence check),
    ``gather_drive_material`` (which item to voice), and the surfaced-marker
    advance after firing — using one query for all three is what makes
    surfacing item N never silently mark item N+1..K as surfaced too (PR
    review: "track surfaced pattern rows individually"). Oldest-first +
    advancing the marker to the JUST-VOICED item's own ``(created_at, id)``
    (never ``now``) means a still-unvoiced newer item always stays above the
    new marker and keeps accumulating ``pattern_insight`` on its own.

    The marker is compared as the PAIR ``(created_at, id)``, not
    ``created_at`` alone: two unsurfaced items can share an identical
    ``created_at`` (same-second bulk insert, or a vault restore), and a
    strict ``created_at > marker`` would then exclude BOTH the one just
    surfaced and its still-unvoiced same-timestamp sibling (PR review: "use a
    row id tie-breaker"). A missing ``pattern_marker_id`` (legacy rows
    written before this column existed) falls back to including every item
    AT the marker timestamp, never excluding one on incomplete information.

    "Active item" guard (superseded_by/distilled_at both unset) mirrors the
    same family of checks IL5/IL6 apply at every MemoryItem query site.
    """
    query = select(MemoryItem).where(
        MemoryItem.user_id == user_id,
        MemoryItem.category == _PATTERN_CATEGORY,
        MemoryItem.superseded_by.is_(None),
        MemoryItem.distilled_at.is_(None),
    )
    if pattern_marker is not None:
        marker_utc = _as_utc(pattern_marker)
        if pattern_marker_id is None:
            query = query.where(MemoryItem.created_at >= marker_utc)
        else:
            query = query.where(
                or_(
                    MemoryItem.created_at > marker_utc,
                    and_(
                        MemoryItem.created_at == marker_utc,
                        MemoryItem.id > pattern_marker_id,
                    ),
                )
            )
    return query.order_by(MemoryItem.created_at.asc(), MemoryItem.id.asc())


def _next_pattern_marker(
    soul_db: Session,
    *,
    user_id: int,
    pattern_marker: datetime | None,
    pattern_marker_id: int | None,
    fallback: datetime,
) -> tuple[datetime, int | None]:
    """The ``(created_at, id)`` pair to advance ``pattern_insight_surfaced_at``/
    ``pattern_insight_surfaced_id`` to after voicing one pattern item — that
    item's OWN identity, never ``now`` — using ``now`` would mark every OTHER
    unvoiced item created before this tick as surfaced too, even though only
    one was ever actually voiced (PR review: "track surfaced pattern rows
    individually"). ``fallback`` covers the should-not-happen case of no row
    resolving (a pattern fire only reaches this point after
    ``gather_drive_material`` already confirmed non-empty material from this
    identical query)."""
    row = soul_db.scalar(
        _unsurfaced_pattern_query(user_id, pattern_marker, pattern_marker_id).limit(1)
    )
    if row is None:
        return fallback, pattern_marker_id
    return row.created_at, row.id


def resolve_drive_signals(
    soul_db: Session,
    runtime_db: Session,
    *,
    user_id: int,
    now: datetime,
    last_user_turn_at: datetime | None,
    pattern_marker: datetime | None,
    pattern_marker_id: int | None = None,
    dream_sharing: str = "on_ask",
) -> tuple[DriveSignals, datetime | None]:
    """Resolve every IL3 grow/reset boolean for one tick.

    Returns ``(signals, latest_message_at)`` — the latter is the newest
    ``RuntimeThread.last_message_at`` seen this tick, for the caller to
    persist as the new ``last_user_turn_at`` marker regardless of whether it
    triggered a reset this cycle (idempotent max, mirrors how
    ``last_fired_at`` is only ever moved forward).
    """
    now_utc = now.astimezone(UTC)
    # Foresight start_date is a user-local CALENDAR date, so the horizon must
    # compare against the LOCAL tick date, not the UTC date — converting to UTC
    # first would shift the window across local midnight in non-UTC zones (an
    # item due "today" could be treated as tomorrow/yesterday). now is already
    # the local-time view; instant math below still uses now_utc.
    today = now.date()

    # unresolved_thread: an OPEN ForesightSignal whose start_date horizon is
    # approaching (within the configured window) or already here. "Open" is
    # the canonical FORESIGHT_ACTIVE_STATUSES set ({active, due, occurred}),
    # NOT just "active": sweep_foresight_lifecycle promotes active -> due the
    # moment an item enters its window (exactly when it's most timely), so an
    # active-only filter would stop the drive growing right when it matters.
    # gather_drive_material builds from the SAME query so the item that grew
    # the drive is exactly the item the fired message talks about.
    horizon_days = settings.initiative_unresolved_thread_horizon_days
    unresolved_thread_open = (
        soul_db.scalar(_open_in_horizon_foresight(user_id, today, horizon_days).limit(1))
        is not None
    )
    # When no open in-horizon thread remains, the source has closed
    # (resolved/cancelled/occurred-then-stale, or none ever existed) — reset
    # the drive so leftover pressure can never fire with no material to speak
    # to. Firing therefore implies an open source in the SAME tick.
    unresolved_thread_resolved = not unresolved_thread_open

    # pattern_insight: an active pattern-synthesis MemoryItem not yet
    # surfaced (created after the last surface marker, or ever if none).
    # Shares its predicate with gather_drive_material and the post-fire
    # marker advance (see _unsurfaced_pattern_query) so all three always
    # agree on the same item.
    pattern_shareable = (
        soul_db.scalar(
            _unsurfaced_pattern_query(user_id, pattern_marker, pattern_marker_id).limit(1)
        )
        is not None
    )

    # relational: days since last contact vs. the cadence proxy (see
    # Settings.initiative_relational_cadence_days docstring — no learned
    # per-relationship cadence model exists yet).
    latest_message_at = runtime_db.scalar(
        select(func.max(RuntimeThread.last_message_at)).where(
            RuntimeThread.user_id == user_id
        )
    )
    latest_message_at = _as_utc(latest_message_at)
    if latest_message_at is None:
        relational_overdue = False
        user_turn_occurred = False
    else:
        days_since_contact = (now_utc - latest_message_at).total_seconds() / 86400.0
        relational_overdue = days_since_contact >= settings.initiative_relational_cadence_days
        marker = _as_utc(last_user_turn_at)
        user_turn_occurred = marker is None or latest_message_at > marker

    # novelty: recent episodes stay topically repetitive while energy is
    # high; resets when a genuinely new topic appears in the newest episode.
    recent_episodes = list(
        soul_db.scalars(
            select(MemoryEpisode)
            .where(MemoryEpisode.user_id == user_id)
            .order_by(MemoryEpisode.id.desc())
            .limit(settings.initiative_novelty_episode_window)
        ).all()
    )
    novelty_repetitive = False
    novel_topic_discussed = False
    if len(recent_episodes) >= 2:
        topic_sets = [_topics_of(ep) for ep in recent_episodes]
        total_mentions = sum(len(topics) for topics in topic_sets)
        distinct_topics: set[str] = set()
        for topics in topic_sets:
            distinct_topics |= topics
        if total_mentions > 0:
            repetition_ratio = 1.0 - (len(distinct_topics) / total_mentions)
            energy_high = _current_energy(runtime_db, user_id=user_id, now=now) >= (
                settings.initiative_novelty_energy_threshold
            )
            novelty_repetitive = (
                repetition_ratio >= settings.initiative_novelty_repetition_threshold
                and energy_high
            )
            newest_topics = topic_sets[0]
            older_topics: set[str] = set()
            for topics in topic_sets[1:]:
                older_topics |= topics
            novel_topic_discussed = bool(newest_topics - older_topics)

    # dream_residue: a share-worthy IL7 dream that hasn't been surfaced yet
    # (see inner_life.dream_edge). Grows the drive until the dream is voiced —
    # but ONLY when dream surfacing is not opted out. dream_sharing="off" means
    # the user never wants dreams mentioned, so a dream must never become an
    # initiative: suppress the grow signal (gather_drive_material returns "" in
    # that case too, so any pressure that accumulated while it was on is reset
    # by the material-less-drive guard instead of firing).
    dream_residue_present = dream_sharing != "off" and (
        soul_db.scalar(
            select(DreamJournal.id)
            .where(
                DreamJournal.user_id == user_id,
                DreamJournal.share_worthy.is_(True),
                DreamJournal.surfaced.is_(False),
            )
            .limit(1)
        )
        is not None
    )

    signals = DriveSignals(
        unresolved_thread_open=unresolved_thread_open,
        pattern_shareable=pattern_shareable,
        relational_overdue=relational_overdue,
        novelty_repetitive=novelty_repetitive,
        dream_residue_present=dream_residue_present,
        user_turn_occurred=user_turn_occurred,
        unresolved_thread_resolved=unresolved_thread_resolved,
        novel_topic_discussed=novel_topic_discussed,
    )
    return signals, latest_message_at


def _current_energy(runtime_db: Session, *, user_id: int, now: datetime) -> float:
    """Current energy for the novelty gate, or a neutral 0.5 when no real
    affect signal exists yet.

    A freshly-seeded default affect state is a config baseline, not a real
    reading (same principle as IL-006's ``resolve_current_affect_magnitude``):
    only read the relaxed energy when an ``AffectStateRow`` has actually been
    persisted, so a brand-new user's novelty gate isn't driven by the seed's
    energy baseline. Relaxes to the CALLER's ``now`` (never the wall clock)
    so this stays deterministic under an injected tick time, exactly like
    every other IL3 edge function.
    """
    try:
        from anima_server.models.runtime_consciousness import AffectStateRow
        from anima_server.services.agent.inner_life.affect import relax
        from anima_server.services.agent.inner_life.store import (
            get_affect_config,
            get_affect_state,
        )

        row = runtime_db.scalar(
            select(AffectStateRow).where(AffectStateRow.user_id == user_id)
        )
        if row is None:
            return 0.5

        config = get_affect_config()
        stored = get_affect_state(runtime_db, user_id=user_id, config=config)
        current = relax(stored, now.astimezone(UTC), config)
        return current.energy
    except Exception:
        return 0.5


def _resolve_affect_line(runtime_db: Session, *, user_id: int, now: datetime) -> str:
    try:
        from anima_server.services.agent.inner_life.affect import relax, render_affect
        from anima_server.services.agent.inner_life.store import (
            get_affect_config,
            get_affect_state,
        )

        config = get_affect_config()
        stored = get_affect_state(runtime_db, user_id=user_id, config=config)
        current = relax(stored, now.astimezone(UTC), config)
        return render_affect(current, previous=stored)
    except Exception:
        logger.debug("Affect line unavailable for initiative for user %s", user_id, exc_info=True)
        return "steady"


def gather_drive_material(
    soul_db: Session,
    *,
    user_id: int,
    drive: str,
    now: datetime,
    pattern_marker: datetime | None = None,
    pattern_marker_id: int | None = None,
    dream_sharing: str = "on_ask",
) -> str:
    """The SPECIFIC accumulated material behind the firing drive — the
    concrete foresight item / pattern finding, not just "a drive fired".
    ``relational``/``novelty`` have no single discrete source row, so they
    get a short descriptive fallback instead of decrypted content.

    ``now`` scopes the ``unresolved_thread`` lookup to the SAME open,
    in-horizon window its grow signal used (see ``_open_in_horizon_foresight``)
    so the message speaks to the item that actually drove the pressure, never
    an unrelated out-of-horizon or start_date-less open row.

    ``pattern_marker`` (``DriveStateRow.pattern_insight_surfaced_at``) scopes
    the ``pattern_insight`` lookup to the SAME oldest-unsurfaced item
    ``resolve_drive_signals`` used for its grow signal (see
    ``_unsurfaced_pattern_query``), so when multiple findings are unsurfaced
    at once, voicing the oldest one never silently skips the newer ones —
    each gets its own turn across successive fires."""
    if drive == DRIVE_UNRESOLVED_THREAD:
        today = now.date()  # local calendar date, matching the grow signal
        horizon_days = settings.initiative_unresolved_thread_horizon_days
        row = soul_db.scalar(
            _open_in_horizon_foresight(user_id, today, horizon_days).limit(1)
        )
        if row is None:
            return ""
        return df(user_id, row.content, table="foresight_signals", field="content")
    if drive == DRIVE_PATTERN_INSIGHT:
        row = soul_db.scalar(
            _unsurfaced_pattern_query(user_id, pattern_marker, pattern_marker_id).limit(1)
        )
        if row is None:
            return ""
        return df(user_id, row.content, table="memory_items", field="content")
    if drive == DRIVE_RELATIONAL:
        return "It has been a while since we last talked."
    if drive == DRIVE_NOVELTY:
        return "Our recent conversations have circled the same few topics."
    if drive == DRIVE_DREAM_RESIDUE:
        # Opted out of dream surfacing -> no material, so the material-less-drive
        # guard resets any lingering dream_residue instead of firing it.
        if dream_sharing == "off":
            return ""
        # The newest share-worthy, unsurfaced IL7 dream (see inner_life.dream_edge).
        row = soul_db.scalar(
            select(DreamJournal)
            .where(
                DreamJournal.user_id == user_id,
                DreamJournal.share_worthy.is_(True),
                DreamJournal.surfaced.is_(False),
            )
            .order_by(DreamJournal.dreamt_at.desc())
            .limit(1)
        )
        if row is None:
            return ""
        return df(user_id, row.narrative, table="dream_journal", field="narrative")
    return ""


# ---------------------------------------------------------------------------
# Edge: rate-cap / unanswered-count queries (provenance log reads)
# ---------------------------------------------------------------------------


def count_unanswered_initiatives(soul_db: Session, *, user_id: int) -> int:
    return int(
        soul_db.scalar(
            select(func.count())
            .select_from(InitiativeLog)
            .where(
                InitiativeLog.user_id == user_id,
                InitiativeLog.delivered.is_(True),
                InitiativeLog.answered.is_(False),
            )
        )
        or 0
    )


def count_recent_fires(soul_db: Session, *, user_id: int, now: datetime) -> tuple[int, int]:
    """(fires in the last 24h, fires in the last 7 days) — only DELIVERED
    initiatives count against the rate cap. A failed generation (no text) or a
    generated-but-undelivered attempt (delivery adapter returned
    ``delivered=False`` / raised) never reached the user, so it must not
    consume their daily/weekly quota. ``delivered=True`` implies
    ``generated_text is not None``, so this also excludes failed generations.
    Re-generation spam on a persistent delivery failure is bounded separately
    by the cooldown (``last_fired_at`` is set on any successful generation)."""
    now_utc = now.astimezone(UTC)
    week_cutoff = now_utc - timedelta(days=7)
    day_cutoff = now_utc - timedelta(hours=24)
    fired_ats = soul_db.scalars(
        select(InitiativeLog.fired_at).where(
            InitiativeLog.user_id == user_id,
            InitiativeLog.delivered.is_(True),
            InitiativeLog.fired_at >= week_cutoff,
        )
    ).all()
    today = sum(1 for ts in fired_ats if _as_utc(ts) >= day_cutoff)
    return today, len(fired_ats)


# ---------------------------------------------------------------------------
# Edge: generation (the ONE LLM seam in all of IL3)
# ---------------------------------------------------------------------------


async def generate_initiative_message(
    soul_db: Session,
    *,
    user_id: int,
    decision: DriveDecision,
    material: str,
    affect_line: str,
) -> str | None:
    """The single small-LLM call IL3 makes. Returns ``None`` on any failure
    or empty output — the caller logs a best-effort attempt and fires
    nothing (PRD: "on generation failure: no message, reset nothing")."""
    from anima_server.services.agent.llm_json import call_llm_for_text
    from anima_server.services.agent.prompt_loader import PromptLoader

    try:
        prompt = PromptLoader.from_db(soul_db, user_id).initiative_message(
            drive=decision.drive,
            material=material,
            affect_line=affect_line,
        )
        text = await call_llm_for_text(
            "You write ONE short unprompted message from the companion's own "
            "initiative. Respond with plain text only: no quotes, no JSON, "
            "no preamble.",
            prompt,
        )
    except Exception:
        logger.warning(
            "Initiative message generation failed for user %s drive %s",
            user_id,
            decision.drive,
            exc_info=True,
        )
        return None

    text = (text or "").strip()
    return text or None


# ---------------------------------------------------------------------------
# Edge: fire (generate + log + deliver), savepoint-isolated
# ---------------------------------------------------------------------------


def _fire(
    soul_db: Session,
    runtime_db: Session,
    *,
    user_id: int,
    decision: DriveDecision,
    now: datetime,
    delivery: InitiativeDelivery,
    pattern_marker: datetime | None = None,
    pattern_marker_id: int | None = None,
    dream_sharing: str = "on_ask",
) -> tuple[bool, InitiativeLog | None]:
    """Attempt generation, ALWAYS write one provenance row (success or a
    logged failed attempt), and deliver on success. Returns ``(delivered,
    log_row)``: ``delivered`` is whether a real message was fired and the
    delivery adapter accepted it — the caller only resets drive pressure and
    ``last_fired_at`` when it is True. ``log_row`` is the pending (uncommitted)
    provenance row so the caller can flip ``delivered`` to True only *after*
    the runtime store's ``PendingInitiative`` is durable (see the two-phase
    commit note in ``tick_initiative_for_user``).

    Each DB effect is wrapped in its own savepoint so a failure in one step
    (e.g. delivery) can never corrupt the other pending writes in this
    per-user session, which commits once at the end (mirrors IL-006's
    per-item savepoint isolation in ``retrieval_feedback.py``).
    """
    material = gather_drive_material(
        soul_db, user_id=user_id, drive=decision.drive, now=now,
        pattern_marker=pattern_marker, pattern_marker_id=pattern_marker_id,
        dream_sharing=dream_sharing,
    )
    # No material -> no fire. A material-backed drive (unresolved_thread /
    # pattern_insight / dream_residue) can cross threshold and then lose its
    # source between accumulation and this tick (the MemoryItem is superseded
    # or distilled, the foresight closes) — gather_drive_material returns "".
    # relational/novelty always return a non-empty descriptive fallback, so an
    # empty string here always means a material-backed drive with nothing left
    # to say. Bail BEFORE the LLM so the "every sentence traces to the specific
    # material" prompt rule can't be violated with a generic message, and
    # without logging a phantom fire. The pressure is left intact (caller only
    # resets on a real fire), so it can fire later if real material reappears.
    if not material.strip():
        logger.debug(
            "No material for drive %s user %s — skipping fire", decision.drive, user_id
        )
        return False, None
    affect_line = _resolve_affect_line(runtime_db, user_id=user_id, now=now)

    try:
        text = asyncio.run(
            generate_initiative_message(
                soul_db,
                user_id=user_id,
                decision=decision,
                material=material,
                affect_line=affect_line,
            )
        )
    except Exception:
        logger.warning(
            "Initiative generation raised for user %s drive %s",
            user_id,
            decision.drive,
            exc_info=True,
        )
        text = None

    log_row: InitiativeLog | None = None
    try:
        with soul_db.begin_nested():
            log_row = InitiativeLog(
                user_id=user_id,
                # Always stored in UTC explicitly: `now` here is the tick's
                # LOCAL-time view (see presence.py's local-time discipline),
                # and SQLite's DateTime(timezone=True) drops tzinfo on
                # read-back, so a naive local value would later get
                # re-interpreted as UTC by `_as_utc` in `count_recent_fires`
                # — silently shifting the daily/weekly rate-cap windows by
                # the server's UTC offset in any non-UTC deployment.
                fired_at=now.astimezone(UTC),
                drive=decision.drive,
                # IL-013: the starvation boosts that influenced this selection
                # ride along under a dedicated key — raw per-drive pressures
                # stay exactly as accumulated, so provenance never lies about
                # pressure, and the fairness override is visible when it acted.
                pressure_snapshot=(
                    {**decision.pressure_snapshot,
                     "starvation": decision.starvation_snapshot}
                    if decision.starvation_snapshot
                    else decision.pressure_snapshot
                ),
                gate_states=decision.gate_states,
                generated_text=(
                    ef(user_id, text, table="initiative_log", field="generated_text")
                    if text
                    else None
                ),
                delivered=False,
                answered=False,
            )
            soul_db.add(log_row)
            soul_db.flush()
    except Exception:
        logger.warning(
            "Initiative provenance log failed for user %s", user_id, exc_info=True
        )
        return False, None

    if text is None or log_row is None:
        return False, log_row

    try:
        with runtime_db.begin_nested():
            result = delivery.deliver(
                runtime_db,
                user_id=user_id,
                drive=decision.drive,
                text=text,
                initiative_log_id=log_row.id,
            )
    except Exception:
        logger.warning(
            "Initiative delivery failed for user %s drive %s",
            user_id,
            decision.drive,
            exc_info=True,
        )
        return False, log_row

    # NB: ``log_row.delivered`` is intentionally left False here. The caller
    # flips it to True only after the runtime store commit persists the
    # ``PendingInitiative`` row, so the provenance log can never over-claim a
    # delivery that a failed runtime commit never actually made durable.
    return result.delivered, log_row


# ---------------------------------------------------------------------------
# Edge: per-user presence-tick sibling
# ---------------------------------------------------------------------------


def tick_initiative_for_user(
    soul_db_factory: Callable[..., Session],
    runtime_db_factory: Callable[..., Session],
    *,
    user_id: int,
    local_now: datetime,
    drive_config: DriveConfig | None = None,
    delivery: InitiativeDelivery | None = None,
) -> bool:
    """Advance this user's drive pressures and, if opted in and every gate
    passes, fire the dominant one. Always advances the accumulators
    (regardless of ``initiative_enabled``) so pressure isn't artificially
    frozen while a user has the feature off and later turns it on.

    Isolated exactly like ``presence._tick_one_user``: any exception here is
    logged and swallowed, never propagated, so one user's failure can never
    abort the sweep or poison another user's session (each user gets its own
    fresh ``soul_db``/``runtime_db`` pair, never shared across users).
    """
    try:
        from anima_server.services.presence_config import get_presence_config_values

        with runtime_db_factory() as runtime_db, soul_db_factory() as soul_db:
            presence_values = get_presence_config_values(soul_db, user_id)

            row = _get_or_seed_drive_row(runtime_db, user_id=user_id, for_update=True)
            delta_hours = max(
                0.0,
                (local_now.astimezone(UTC) - _as_utc(row.updated_at)).total_seconds()
                / 3600.0,
            )
            signals, latest_message_at = resolve_drive_signals(
                soul_db,
                runtime_db,
                user_id=user_id,
                now=local_now,
                last_user_turn_at=row.last_user_turn_at,
                pattern_marker=row.pattern_insight_surfaced_at,
                pattern_marker_id=row.pattern_insight_surfaced_id,
                dream_sharing=presence_values.dream_sharing,
            )
            updated_pressures = advance_drives(
                _pressures_of(row),
                signals,
                delta_hours,
                drive_config or get_drive_config(),
            )
            _apply_pressures(row, updated_pressures, now=local_now)
            # IL-013 bookkeeping (PR #128 review): mutable copy of the
            # persisted per-drive loss counters; written back as a fresh dict
            # (JSON columns don't track in-place mutation) wherever this tick
            # changes them. A signal-driven hard reset just zeroed the drive's
            # pressure via advance_drives, so its loss history goes with it —
            # this runs BEFORE the initiative-disabled early return below,
            # which commits without ever reaching the selection loop, so a
            # disabled user's stale boost can't survive to a later opt-in.
            starvation_losses: dict[str, int] = dict(row.starvation_losses or {})
            reset_cleared = False
            for name in signal_reset_drives(signals):
                if starvation_losses.pop(name, None) is not None:
                    reset_cleared = True
            if reset_cleared:
                row.starvation_losses = dict(starvation_losses)
            if latest_message_at is not None and (
                row.last_user_turn_at is None
                or latest_message_at > _as_utc(row.last_user_turn_at)
            ):
                row.last_user_turn_at = latest_message_at
            row.unanswered_initiatives = count_unanswered_initiatives(soul_db, user_id=user_id)

            # Drives kept advancing above regardless; firing requires BOTH the
            # master Presence switch and the initiative opt-in (mirrors
            # get_gate_config's enabled gate — a paused user never fires).
            if not (presence_values.enabled and presence_values.initiative_enabled):
                runtime_db.commit()
                return True

            closeness = resolve_closeness_signal(runtime_db, user_id=user_id, now=local_now)
            fires_today, fires_this_week = count_recent_fires(
                soul_db, user_id=user_id, now=local_now
            )
            gate_config = get_gate_config(presence_values)
            # Select the drive to fire. should_fire() always returns the
            # highest-pressure qualifying drive; but a material-backed drive
            # (e.g. pattern_insight) can sit above threshold with NO material
            # left — its source MemoryItem was superseded/distilled after it
            # accumulated. Firing it is impossible (no material), yet leaving it
            # dominant would monopolize every tick for its whole leak window
            # (~85h at tau=240h) and starve lower-pressure drives that DO have
            # material. So reset any material-less dominant drive and re-select,
            # bounded by the drive count. The reset is persisted so the stale
            # pressure doesn't just reappear next tick.
            decision = None
            for _ in range(len(DRIVE_NAMES)):
                record = DriveRecord(
                    pressures=updated_pressures,
                    last_fired_at=row.last_fired_at,
                    last_user_turn_at=row.last_user_turn_at,
                    unanswered_initiatives=row.unanswered_initiatives,
                    starvation_losses=starvation_losses,
                )
                candidate = should_fire(
                    record,
                    gate_config,
                    local_now,
                    closeness,
                    fires_today=fires_today,
                    fires_this_week=fires_this_week,
                )
                if candidate is None:
                    break
                if gather_drive_material(
                    soul_db, user_id=user_id, drive=candidate.drive, now=local_now,
                    pattern_marker=row.pattern_insight_surfaced_at,
                    pattern_marker_id=row.pattern_insight_surfaced_id,
                    dream_sharing=presence_values.dream_sharing,
                ).strip():
                    decision = candidate
                    break
                updated_pressures = reset_drive(updated_pressures, candidate.drive)
                _apply_pressures(row, updated_pressures, now=local_now)
                # Hard reset means the material is gone — its starvation
                # history goes with it (a boost earned on vanished material
                # must not jump-start the drive's next accumulation).
                if starvation_losses.pop(candidate.drive, None) is not None:
                    row.starvation_losses = dict(starvation_losses)

            if decision is None:
                runtime_db.commit()
                return True

            # Require an active memories DEK before firing. `_fire` decrypts
            # foresight/pattern material via `df()` and encrypts the generated
            # text via `ef()` — both fail OPEN (return the value unchanged)
            # when no DEK is cached, which would let a foresight/pattern fire
            # prompt the LLM with raw ciphertext AND commit an unencrypted
            # `initiative_log.generated_text` regardless of which drive fired.
            # Skip this tick entirely (pressure stays elevated, nothing
            # logged) until the user's session is unlocked again; the next
            # tick with an active DEK fires normally.
            if get_active_dek(user_id, DOMAIN_MEMORIES) is None:
                runtime_db.commit()
                return True

            fired, log_row = _fire(
                soul_db,
                runtime_db,
                user_id=user_id,
                decision=decision,
                now=local_now,
                delivery=delivery or PendingInitiativeDelivery(),
                pattern_marker=row.pattern_insight_surfaced_at,
                pattern_marker_id=row.pattern_insight_surfaced_id,
                dream_sharing=presence_values.dream_sharing,
            )
            # A successful GENERATION (text produced, log row written) starts
            # the cooldown even if DELIVERY then failed — otherwise, since a
            # failed delivery no longer consumes the rate cap
            # (count_recent_fires counts only delivered rows), a persistent
            # delivery failure would re-generate a fresh LLM message every
            # tick. `generated_text` is None only when generation itself
            # failed, so this covers exactly the "generated but maybe not
            # delivered" case.
            generated = log_row is not None and log_row.generated_text is not None
            if generated:
                row.last_fired_at = local_now.astimezone(UTC)
            # Surfacing the material and resetting the drive happen ONLY on an
            # actual delivery — a message the user never received must not mark
            # its pattern surfaced or drain the drive that still needs voicing.
            if fired:
                if decision.drive == DRIVE_PATTERN_INSIGHT:
                    # Advance to the JUST-VOICED item's own (created_at, id),
                    # not `now` — otherwise every other still-unvoiced pattern
                    # finding created before this tick (including a same-
                    # timestamp sibling) would be silently marked surfaced
                    # too, even though only one was ever actually voiced.
                    marker_at, marker_id = _next_pattern_marker(
                        soul_db,
                        user_id=user_id,
                        pattern_marker=row.pattern_insight_surfaced_at,
                        pattern_marker_id=row.pattern_insight_surfaced_id,
                        fallback=local_now.astimezone(UTC),
                    )
                    row.pattern_insight_surfaced_at = marker_at
                    row.pattern_insight_surfaced_id = marker_id
                if decision.drive == DRIVE_DREAM_RESIDUE:
                    # Mark the just-voiced dream surfaced so it stops re-raising
                    # dream_residue (mirrors pattern_insight's surface marker).
                    dream_row = soul_db.scalar(
                        select(DreamJournal)
                        .where(
                            DreamJournal.user_id == user_id,
                            DreamJournal.share_worthy.is_(True),
                            DreamJournal.surfaced.is_(False),
                        )
                        .order_by(DreamJournal.dreamt_at.desc())
                        .limit(1)
                    )
                    if dream_row is not None:
                        dream_row.surfaced = True
                # IL-013: every drive that qualified (raw pressure >= theta)
                # but lost this DELIVERED fire accrues one loss toward its
                # future ranking boost; the winner's history clears. Gate
                # failures and undelivered generations count for nobody —
                # nothing was actually chosen over anything.
                for name in DRIVE_NAMES:
                    if name == decision.drive:
                        starvation_losses.pop(name, None)
                    elif getattr(updated_pressures, name) >= gate_config.theta(name):
                        starvation_losses[name] = starvation_losses.get(name, 0) + 1
                row.starvation_losses = dict(starvation_losses)
                _apply_pressures(
                    row, reset_drive(updated_pressures, decision.drive), now=local_now
                )

            # Two-phase commit across the split soul/runtime stores. Commit the
            # soul store (provenance log) FIRST so the runtime store's
            # ``PendingInitiative`` — the thing a client actually polls and sees
            # — can never become durable without its ``InitiativeLog`` already
            # committed: a delivered message always has provenance. Only after
            # the runtime commit makes the delivery durable do we flip
            # ``log_row.delivered`` to True, so the log never over-claims a
            # delivery a failed runtime commit would have rolled back.
            soul_db.commit()
            runtime_db.commit()
            if fired and log_row is not None:
                # Residual two-phase-commit window (inherent to spanning two
                # physical DBs without an outbox): if THIS commit crashes after
                # the runtime commit already made the delivery durable, the log
                # under-claims (delivered stays False for a delivered message).
                # This fails safe — it can never cause over-firing: rate caps
                # (`count_recent_fires`) count by `generated_text is not None`,
                # not `delivered`, and `last_fired_at` was already committed
                # above, so the cooldown gate still holds. The only effect is
                # `count_unanswered_initiatives` (backoff) undercounting by one.
                # A true fix (transactional outbox / reconciliation) is a
                # cross-cutting follow-up, not scoped to IL3.
                log_row.delivered = True
                soul_db.commit()
        return True
    except Exception:
        logger.warning("Initiative tick failed for user %s", user_id, exc_info=True)
        return False
