"""IL3 — Drive pressure accumulators (pure).

Five named scalars ``P_i in [0, 1]`` (PRD "IL3 — Drive Accumulators and Push
Initiative"), each a leaky integrator: while its "grows when" condition holds
it climbs at a per-drive rate scaled by elapsed hours; while the condition is
absent it leaks gently back toward 0 (an exponential decay, mirroring IL1's
closed-form relaxation) rather than staying frozen; an explicit "resets when"
event hard-zeroes it regardless of the other two forces.

Everything here is plain arithmetic over already-resolved booleans — no DB,
no LLM, no datetime.now() (Ī”t is always injected by the edge caller). Signal
resolution (querying ForesightSignal, pattern MemoryItem rows, contact
cadence, topic diversity, ...) and persistence both live in ``initiative.py``
at the edge, exactly like IL1's ``affect.py`` (pure) / ``store.py`` +
``presence.py`` (edges) split.

Two of the five drives (``pattern_insight``, ``dream_residue``) reset ONLY
when their material is actually surfaced — i.e. when an initiative fires on
them. That is a hard zero applied by the edge via ``reset_drive`` at fire
time, not a boolean this module's signals carry, so ``advance_drives`` never
resets them itself (``reset=False`` unconditionally for those two below).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

DRIVE_UNRESOLVED_THREAD = "unresolved_thread"
DRIVE_PATTERN_INSIGHT = "pattern_insight"
DRIVE_RELATIONAL = "relational"
DRIVE_NOVELTY = "novelty"
DRIVE_DREAM_RESIDUE = "dream_residue"

DRIVE_NAMES: tuple[str, ...] = (
    DRIVE_UNRESOLVED_THREAD,
    DRIVE_PATTERN_INSIGHT,
    DRIVE_RELATIONAL,
    DRIVE_NOVELTY,
    DRIVE_DREAM_RESIDUE,
)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True, slots=True)
class DriveState:
    """The five IL3 pressure scalars, each clamped to [0, 1]."""

    unresolved_thread: float = 0.0
    pattern_insight: float = 0.0
    relational: float = 0.0
    novelty: float = 0.0
    dream_residue: float = 0.0

    def as_dict(self) -> dict[str, float]:
        """All five pressures at once — the provenance snapshot shape."""
        return {name: getattr(self, name) for name in DRIVE_NAMES}


@dataclass(frozen=True, slots=True)
class DriveSignals:
    """Edge-resolved booleans for one tick.

    Each maps directly onto one row of the PRD IL3 table:

    - ``unresolved_thread_open``: an open ForesightSignal's horizon is
      approaching (grows ``unresolved_thread``).
    - ``pattern_shareable``: a shareable pattern-synthesis finding exists
      that has not yet been surfaced (grows ``pattern_insight``).
    - ``relational_overdue``: days since last contact exceed the (learned or
      proxy) cadence (grows ``relational``).
    - ``novelty_repetitive``: recent sessions stay topically repetitive
      while energy is high (grows ``novelty``).
    - ``dream_residue_present``: a share-worthy dream exists (grows
      ``dream_residue``) — always ``False`` until IL-007 exists; wired but
      dormant.
    - ``user_turn_occurred``: any user turn happened since the last tick —
      resets ``unresolved_thread`` and ``relational``.
    - ``unresolved_thread_resolved``: the foresight source that fed
      ``unresolved_thread`` is no longer an open item in the horizon (it was
      resolved/cancelled/occurred-then-stale, or none exists) — also resets
      ``unresolved_thread``, so the pressure can never linger (and fire) after
      its material is gone. Distinct from ``unresolved_thread_open`` being
      False: a bare ``DriveSignals()`` leaves this False so the pure leak path
      is unchanged; the edge sets it explicitly.
    - ``novel_topic_discussed``: a genuinely new topic came up — resets
      ``novelty``.
    """

    unresolved_thread_open: bool = False
    pattern_shareable: bool = False
    relational_overdue: bool = False
    novelty_repetitive: bool = False
    dream_residue_present: bool = False
    user_turn_occurred: bool = False
    unresolved_thread_resolved: bool = False
    novel_topic_discussed: bool = False


@dataclass(frozen=True, slots=True)
class DriveConfig:
    """Per-drive growth rates (pressure/hour while growing) and the shared
    leak time constant (hours) for the non-growing decay."""

    growth_unresolved_thread: float = 0.10
    growth_pattern_insight: float = 0.08
    growth_relational: float = 0.05
    growth_novelty: float = 0.05
    growth_dream_residue: float = 0.05
    leak_tau_hours: float = 240.0


DEFAULT_DRIVE_CONFIG = DriveConfig()


def _advance_one(
    value: float,
    *,
    grow: bool,
    reset: bool,
    growth_rate: float,
    leak_tau_hours: float,
    delta_hours: float,
) -> float:
    if reset:
        return 0.0
    if grow:
        return _clamp01(value + growth_rate * delta_hours)
    if delta_hours <= 0.0 or leak_tau_hours <= 0.0:
        return _clamp01(value)
    return _clamp01(value * math.exp(-delta_hours / leak_tau_hours))


def signal_reset_drives(signals: DriveSignals) -> tuple[str, ...]:
    """The drives a tick's signals hard-zero — the SINGLE definition of the
    signal->reset mapping. ``advance_drives`` applies it to the pressures,
    and the IL-013 edge bookkeeping applies it to the starvation-loss map:
    a reset drive's loss history must be cleared along with its pressure,
    so a ranking boost earned against old, now-addressed material can never
    jump-start the drive's next, unrelated accumulation.

    - ``unresolved_thread``: a user turn OR the source thread closing — the
      latter guarantees the pressure can't linger and fire once its material
      is gone (see ``DriveSignals.unresolved_thread_resolved``).
    - ``relational``: a user turn (contact happened).
    - ``novelty``: a genuinely new topic came up.
    - ``pattern_insight``/``dream_residue``: never here — they reset only
      when surfaced (``reset_drive`` at fire time; see module docstring).
    """
    resets: list[str] = []
    if signals.user_turn_occurred or signals.unresolved_thread_resolved:
        resets.append(DRIVE_UNRESOLVED_THREAD)
    if signals.user_turn_occurred:
        resets.append(DRIVE_RELATIONAL)
    if signals.novel_topic_discussed:
        resets.append(DRIVE_NOVELTY)
    return tuple(resets)


def advance_drives(
    state: DriveState,
    signals: DriveSignals,
    delta_hours: float,
    config: DriveConfig = DEFAULT_DRIVE_CONFIG,
) -> DriveState:
    """One tick's leaky-integrator update for all five drives.

    ``delta_hours`` is the elapsed time since the last tick for this user
    (negative values are clamped to 0 — a clock going backwards must never
    shrink a growing pressure or manufacture decay). Pure: no I/O, no clock
    reads. Hard resets follow ``signal_reset_drives`` exactly.
    """
    delta_hours = max(0.0, delta_hours)
    resets = frozenset(signal_reset_drives(signals))
    return DriveState(
        unresolved_thread=_advance_one(
            state.unresolved_thread,
            grow=signals.unresolved_thread_open,
            reset=DRIVE_UNRESOLVED_THREAD in resets,
            growth_rate=config.growth_unresolved_thread,
            leak_tau_hours=config.leak_tau_hours,
            delta_hours=delta_hours,
        ),
        pattern_insight=_advance_one(
            state.pattern_insight,
            grow=signals.pattern_shareable,
            reset=False,  # only resets when surfaced — see module docstring
            growth_rate=config.growth_pattern_insight,
            leak_tau_hours=config.leak_tau_hours,
            delta_hours=delta_hours,
        ),
        relational=_advance_one(
            state.relational,
            grow=signals.relational_overdue,
            reset=DRIVE_RELATIONAL in resets,
            growth_rate=config.growth_relational,
            leak_tau_hours=config.leak_tau_hours,
            delta_hours=delta_hours,
        ),
        novelty=_advance_one(
            state.novelty,
            grow=signals.novelty_repetitive,
            reset=DRIVE_NOVELTY in resets,
            growth_rate=config.growth_novelty,
            leak_tau_hours=config.leak_tau_hours,
            delta_hours=delta_hours,
        ),
        dream_residue=_advance_one(
            state.dream_residue,
            grow=signals.dream_residue_present,
            reset=False,  # only resets when surfaced — see module docstring
            growth_rate=config.growth_dream_residue,
            leak_tau_hours=config.leak_tau_hours,
            delta_hours=delta_hours,
        ),
    )


def reset_drive(state: DriveState, drive: str) -> DriveState:
    """Hard-zero one drive: the "surfaced" reset applied when an initiative
    fires on it (every drive resets when its material is surfaced)."""
    if drive not in DRIVE_NAMES:
        raise ValueError(f"Unknown drive: {drive!r}")
    return replace(state, **{drive: 0.0})
