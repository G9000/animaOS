"""Pure affect-state dynamics (IL1): valence, arousal, energy.

No DB, no I/O, no LLM calls — every function here is `(state, event, Δt) →
state` arithmetic, safe to call from tests, ticks, or offline catch-up alike.
Persistence lives in `store.py`; wiring lives in `consolidation.py` and
`proactive.py`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime

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


def circadian_arousal_baseline(
    local_hour: float,
    config: AffectConfig = DEFAULT_AFFECT_CONFIG,
) -> float:
    """Fixed sinusoidal baseline arousal over local hour-of-day.

    Trough at 03:00, peak at 15:00 (12h apart), midline/amplitude per config.
    """
    phase = 2.0 * math.pi * (local_hour - config.circadian_peak_hour) / 24.0
    return config.circadian_midline + config.circadian_amplitude * math.cos(phase)


def relax(
    state: AffectState,
    now: datetime,
    config: AffectConfig = DEFAULT_AFFECT_CONFIG,
) -> AffectState:
    """Relax every component toward its baseline in closed form.

    `x(t+dt) = b + (x(t) - b) * exp(-dt/tau_x)`. `now` is treated as already
    being in the local timezone the circadian baseline should use — timezone
    resolution is a side-effect concern and happens at the wiring edge, not
    here. Arousal's baseline is the circadian sinusoid shifted up by the
    allostatic load (`arousal_baseline_shift`).
    """
    dt_hours = (now - state.updated_at).total_seconds() / 3600.0
    if dt_hours <= 0:
        return state if dt_hours < 0 else replace(state, updated_at=now)

    local_hour = (
        now.hour + now.minute / 60.0 + now.second / 3600.0 + now.microsecond / 3.6e9
    )
    arousal_baseline = (
        circadian_arousal_baseline(local_hour, config) + state.arousal_baseline_shift
    )

    valence = config.baseline_valence + (state.valence - config.baseline_valence) * math.exp(
        -dt_hours / config.tau_valence_hours
    )
    arousal = arousal_baseline + (state.arousal - arousal_baseline) * math.exp(
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
