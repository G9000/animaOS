"""Pure affect-state dynamics (IL1): valence, arousal, energy.

No DB, no I/O, no LLM calls — every function here is `(state, event, Δt) →
state` arithmetic, safe to call from tests, ticks, or offline catch-up alike.
Persistence lives in `store.py`; wiring lives in `consolidation.py` and
`proactive.py`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

VALENCE_BOUNDS = (-1.0, 1.0)
AROUSAL_BOUNDS = (0.0, 1.0)
ENERGY_BOUNDS = (0.0, 1.0)

# Per-turn emotional-signal deltas are capped before they touch the state,
# regardless of how strong the detected signal was.
TURN_DELTA_CAP = 0.15


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass(frozen=True, slots=True)
class AffectConfig:
    """Time constants, baselines, and thresholds for affect dynamics.

    Defaults are the PRD IL1 values. Taus may be overridden per-deployment
    via `Settings` (see `store.get_affect_config`); baselines and thresholds
    are fixed engineering constants.
    """

    tau_valence_hours: float = 36.0
    tau_arousal_hours: float = 6.0
    tau_energy_hours: float = 18.0

    baseline_valence: float = 0.0
    baseline_energy: float = 0.6

    circadian_midline: float = 0.35
    circadian_amplitude: float = 0.1
    circadian_peak_hour: float = 15.0

    allostatic_arousal_threshold: float = 0.7
    allostatic_sustained_hours: float = 48.0
    allostatic_max_shift: float = 0.05
    allostatic_decay_tau_hours: float = 24.0 * 7.0
    allostatic_recovery_drain_rate: float = 0.5
    high_arousal_hours_cap: float = 96.0


DEFAULT_AFFECT_CONFIG = AffectConfig()


@dataclass(frozen=True, slots=True)
class AffectState:
    """The persisted affect vector plus the slow-moving allostatic state."""

    valence: float
    arousal: float
    energy: float
    updated_at: datetime
    arousal_baseline_shift: float = 0.0
    high_arousal_hours: float = 0.0


def apply_turn_deltas(
    state: AffectState,
    valence_delta: float,
    arousal_delta: float,
    energy_delta: float,
) -> AffectState:
    """Apply clamped per-turn deltas to `state`.

    Each delta is clamped to ±`TURN_DELTA_CAP` before being applied; the
    resulting components are then clamped to their own bounds.
    """
    v = _clamp(valence_delta, -TURN_DELTA_CAP, TURN_DELTA_CAP)
    a = _clamp(arousal_delta, -TURN_DELTA_CAP, TURN_DELTA_CAP)
    e = _clamp(energy_delta, -TURN_DELTA_CAP, TURN_DELTA_CAP)
    return replace(
        state,
        valence=_clamp(state.valence + v, *VALENCE_BOUNDS),
        arousal=_clamp(state.arousal + a, *AROUSAL_BOUNDS),
        energy=_clamp(state.energy + e, *ENERGY_BOUNDS),
    )


_CIRCADIAN_OMEGA = 2.0 * math.pi / 24.0  # radians per hour


def circadian_arousal_baseline(
    local_hour: float,
    config: AffectConfig = DEFAULT_AFFECT_CONFIG,
) -> float:
    """Fixed sinusoidal baseline arousal `b(t)` over local hour-of-day.

    Trough at 03:00, peak at 15:00 (12h apart), midline/amplitude per config.
    """
    theta = _CIRCADIAN_OMEGA * (local_hour - config.circadian_peak_hour)
    return config.circadian_midline + config.circadian_amplitude * math.cos(theta)


def _circadian_particular_arousal(
    local_hour: float,
    shift: float,
    config: AffectConfig,
) -> float:
    """Particular solution `xp(t)` of the arousal ODE dx/dt = (b(t) - x)/tau.

    xp(t) = m + A/(1 + (wt)^2) * [cos(theta) + wt*sin(theta)] with
    theta = w*(local_hour - peak), w = 2*pi/24, m = midline + allostatic
    shift. With wt = pi/2 (tau = 6 h) equilibrium arousal tracks a damped,
    delayed daily wave: resultant amplitude A/sqrt(1+(wt)^2) ~= 0.054
    (gain per quadrature component A/(1+(wt)^2) ~= 0.029), phase lag
    atan(wt) ~= 3.8 h — physically sensible and exactly composable.
    """
    omega_tau = _CIRCADIAN_OMEGA * config.tau_arousal_hours
    theta = _CIRCADIAN_OMEGA * (local_hour - config.circadian_peak_hour)
    gain = config.circadian_amplitude / (1.0 + omega_tau * omega_tau)
    return (
        config.circadian_midline
        + shift
        + gain * (math.cos(theta) + omega_tau * math.sin(theta))
    )


def _local_hour(moment: datetime) -> float:
    return (
        moment.hour
        + moment.minute / 60.0
        + moment.second / 3600.0
        + moment.microsecond / 3.6e9
    )


def _elapsed_hours(start: datetime, end: datetime) -> float:
    """True elapsed hours between two datetimes.

    Aware pairs are normalized to UTC first: CPython's intra-zone rule
    makes subtraction of two aware datetimes sharing a tzinfo *wall-clock*
    arithmetic, which mis-measures any interval spanning a DST transition
    by the DST delta. Naive pairs subtract directly (pre-existing
    behavior); mixed naive/aware raises, as before.
    """
    if start.tzinfo is not None and end.tzinfo is not None:
        return (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() / 3600.0
    return (end - start).total_seconds() / 3600.0


def relax(
    state: AffectState,
    now: datetime,
    config: AffectConfig = DEFAULT_AFFECT_CONFIG,
) -> AffectState:
    """Relax every component toward its baseline in closed form.

    Valence and energy have constant baselines: `x(t+dt) = b + (x(t) - b)
    * exp(-dt/tau_x)`. Arousal's baseline oscillates (circadian), so it
    uses the exact solution of the linear ODE dx/dt = (b(t) - x)/tau:
    `x(t) = xp(t) + (x(t0) - xp(t0)) * exp(-dt/tau)` with the attenuated,
    phase-lagged particular solution `xp` — exactly semigroup, so one gap
    application equals any tick composition even across moving baseline.
    `now` is treated as already being in the local timezone the circadian
    baseline should use — timezone resolution is a side-effect concern and
    happens at the wiring edge, not here.
    """
    dt_hours = _elapsed_hours(state.updated_at, now)
    if dt_hours <= 0:
        return state if dt_hours < 0 else replace(state, updated_at=now)

    xp_now = _circadian_particular_arousal(
        _local_hour(now), state.arousal_baseline_shift, config
    )
    xp_then = _circadian_particular_arousal(
        _local_hour(state.updated_at), state.arousal_baseline_shift, config
    )

    valence = config.baseline_valence + (state.valence - config.baseline_valence) * math.exp(
        -dt_hours / config.tau_valence_hours
    )
    arousal = xp_now + (state.arousal - xp_then) * math.exp(
        -dt_hours / config.tau_arousal_hours
    )
    energy = config.baseline_energy + (state.energy - config.baseline_energy) * math.exp(
        -dt_hours / config.tau_energy_hours
    )

    return replace(
        state,
        valence=_clamp(valence, *VALENCE_BOUNDS),
        arousal=_clamp(arousal, *AROUSAL_BOUNDS),
        energy=_clamp(energy, *ENERGY_BOUNDS),
        updated_at=now,
    )


def _advance(moment: datetime, hours: float) -> datetime:
    """Advance `moment` by absolute elapsed hours.

    Aware datetimes advance in UTC and convert back, so the result carries
    the correct wall clock even when the interval crosses a DST transition
    (naive wall-clock addition would silently shift the instant by the DST
    delta). Naive datetimes are advanced directly.
    """
    if moment.tzinfo is None:
        return moment + timedelta(hours=hours)
    return (moment.astimezone(UTC) + timedelta(hours=hours)).astimezone(moment.tzinfo)


def arousal_threshold_crossing_time(
    state: AffectState,
    config: AffectConfig = DEFAULT_AFFECT_CONFIG,
    threshold: float | None = None,
) -> float:
    """Hours until `relax`'s arousal trajectory first drops to `threshold`.

    Solves x(t) = xp(t) + (x0 - xp(t0)) * exp(-t/tau) (the exact arousal
    solution used by `relax`) for the first t with x(t) <= threshold
    (default: the allostatic threshold). The above-threshold region is a
    single leading interval [0, t*): the circadian equilibrium band tops
    out well below the allostatic threshold, so at any crossing the
    transient is decaying at (threshold - xp(t))/tau >= ~0.04/h while xp
    itself moves at most amplitude * omega ~= 0.014/h — the trajectory
    cannot re-cross once below. Bisection to 1e-9 h; bounded pure
    arithmetic, O(1), no DB. Returns 0.0 when already at/below threshold
    and +inf if the equilibrium band itself sits above the threshold
    (impossible for the default config; guards custom thresholds).

    `state.updated_at`'s zone is honored exactly: elapsed time advances in
    UTC while circadian phase reads the wall clock at each probed instant,
    so DST transitions inside the solve window land in the right place.
    """
    thr = config.allostatic_arousal_threshold if threshold is None else threshold
    if state.arousal <= thr:
        return 0.0

    tau = config.tau_arousal_hours
    xp0 = _circadian_particular_arousal(
        _local_hour(state.updated_at), state.arousal_baseline_shift, config
    )
    transient0 = state.arousal - xp0

    def arousal_at(t_hours: float) -> float:
        moment = _advance(state.updated_at, t_hours)
        xp_t = _circadian_particular_arousal(
            _local_hour(moment), state.arousal_baseline_shift, config
        )
        return xp_t + transient0 * math.exp(-t_hours / tau)

    hi = tau
    while arousal_at(hi) > thr:
        hi *= 2.0
        if hi > 64.0 * tau:
            return math.inf
    lo = 0.0
    while hi - lo > 1e-9:
        mid = 0.5 * (lo + hi)
        if arousal_at(mid) > thr:
            lo = mid
        else:
            hi = mid
    return hi


def update_allostatic_shift(
    state: AffectState,
    sustained_high_arousal_hours: float,
    config: AffectConfig = DEFAULT_AFFECT_CONFIG,
) -> AffectState:
    """Update the allostatic (baseline-shifting) load.

    `sustained_high_arousal_hours` is the elapsed hours since the last
    update. When `state.arousal` is currently above the allostatic
    threshold, those hours accumulate onto `high_arousal_hours`; otherwise
    the accumulator drains at half rate — load is cumulative and recovers
    gradually, per the PRD's "until a recovery period passes", rather than
    resetting on the first calm moment. Once the cumulative total exceeds
    the sustained-hours threshold, `arousal_baseline_shift` is raised toward
    its cap in proportion to how far past the threshold the load has run;
    otherwise the shift decays back toward zero with a 7-day time constant.
    """
    dt_hours = max(0.0, sustained_high_arousal_hours)

    if state.arousal > config.allostatic_arousal_threshold:
        high_hours = _clamp(
            state.high_arousal_hours + dt_hours, 0.0, config.high_arousal_hours_cap
        )
    else:
        high_hours = _clamp(
            state.high_arousal_hours - config.allostatic_recovery_drain_rate * dt_hours,
            0.0,
            config.high_arousal_hours_cap,
        )

    if high_hours > config.allostatic_sustained_hours:
        excess = high_hours - config.allostatic_sustained_hours
        span = config.high_arousal_hours_cap - config.allostatic_sustained_hours
        shift = config.allostatic_max_shift * _clamp(excess / span, 0.0, 1.0)
    else:
        shift = state.arousal_baseline_shift * math.exp(
            -dt_hours / config.allostatic_decay_tau_hours
        )

    return replace(
        state,
        high_arousal_hours=high_hours,
        arousal_baseline_shift=_clamp(shift, 0.0, config.allostatic_max_shift),
    )


# --- Rendering: adjectives + trajectory, never raw numbers -----------------

_VALENCE_LOW = -0.3
_VALENCE_HIGH = 0.3
_AROUSAL_LOW = 0.35
_AROUSAL_HIGH = 0.65
_ENERGY_LOW = 0.35
_ENERGY_HIGH = 0.75
_DRIFT_STABLE_THRESHOLD = 0.02

# The full rendering vocabulary in one place: ("adjective", valence band,
# arousal band) picks the core adjective, ("energy", band) prefixes an
# energy qualifier, ("trajectory", drift sign) closes the phrase.
_VOCABULARY: dict[tuple[str, ...], str] = {
    ("adjective", "low", "low"): "subdued",
    ("adjective", "low", "mid"): "unsettled",
    ("adjective", "low", "high"): "agitated",
    ("adjective", "mid", "low"): "quiet",
    ("adjective", "mid", "mid"): "settled",
    ("adjective", "mid", "high"): "keyed up",
    ("adjective", "high", "low"): "content",
    ("adjective", "high", "mid"): "bright",
    ("adjective", "high", "high"): "elated",
    ("energy", "low"): "tired and ",
    ("energy", "mid"): "",
    ("energy", "high"): "energized and ",
    ("trajectory", "rising"): "slowly brightening",
    ("trajectory", "falling"): "slowly dimming",
    ("trajectory", "stable"): "holding steady",
}


def _band(value: float, low: float, high: float) -> str:
    if value < low:
        return "low"
    if value > high:
        return "high"
    return "mid"


def render_affect(
    state: AffectState,
    *,
    previous: AffectState | None = None,
    drift: float | None = None,
) -> str:
    """Render the affect state as a short adjective + trajectory phrase.

    Deterministic mapping over (valence, arousal, energy) bands plus the
    sign of recent drift. Never emits raw numbers — prompts get adjectives,
    not scalars. Drift is either passed explicitly or derived from
    `previous.valence`; with neither, the trajectory reads as stable.
    """
    valence_band = _band(state.valence, _VALENCE_LOW, _VALENCE_HIGH)
    arousal_band = _band(state.arousal, _AROUSAL_LOW, _AROUSAL_HIGH)
    energy_band = _band(state.energy, _ENERGY_LOW, _ENERGY_HIGH)
    adjective = _VOCABULARY[("adjective", valence_band, arousal_band)]
    energy_qualifier = _VOCABULARY[("energy", energy_band)]

    effective_drift = drift if drift is not None else (
        state.valence - previous.valence if previous is not None else 0.0
    )
    if effective_drift > _DRIFT_STABLE_THRESHOLD:
        trajectory = _VOCABULARY[("trajectory", "rising")]
    elif effective_drift < -_DRIFT_STABLE_THRESHOLD:
        trajectory = _VOCABULARY[("trajectory", "falling")]
    else:
        trajectory = _VOCABULARY[("trajectory", "stable")]

    return f"{energy_qualifier}{adjective}, {trajectory}"
