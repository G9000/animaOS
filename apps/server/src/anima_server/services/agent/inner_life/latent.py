"""Pure latent-trace scoring, fold, and decay math (IL4).

No DB, no I/O, no LLM calls — every function here is arithmetic over plain
scalars, safe to call from tests or the promotion/decay/crystallization edges
alike. Persistence lives in ``services/agent/latent_traces.py``; wiring lives
in ``soul_writer.py`` (the promotion gate) and ``sleep_agent.py``/
``sleep_tasks.py`` (weekly decay + crystallization).

Score formula and thresholds are PRD IL4 (docs/prds/presence/inner-life-v1.md
"IL4 — Latent Trace Crystallization"):

    s = clamp01(0.6 * importance/5 + 0.3 * emotional_salience + 0.1 * evidence_strength)

``latent_promotion_threshold`` (theta_p) is calibrated so importance >= 2
candidates promote exactly as they did before IL4 existed — the worst case
(importance=2, zero emotional salience, default evidence_strength=0.8) scores
0.32, just above the 0.30 default. Candidates scoring in
``[floor_ratio * theta_p, theta_p)`` fold into a latent trace instead of being
silently dropped; below the floor they are rejected exactly as an
unconditional "drop" would have been (there was no scoring path before IL4,
so anything this weak was implicitly discarded already).

The trace update is an additive leaky integrator, NOT an exponential moving
average: ``weight <- min(1.0, weight + fold_rate * s)``, with weekly decay
(``weight *= weekly_decay``) as the "leak". An EMA would converge toward the
per-event score and never cross the crystallization threshold no matter how
many times the same weak signal repeated — the opposite of what IL4 exists
to fix (this was a PR review finding on the PRD).
"""

from __future__ import annotations

from dataclasses import dataclass

# Below this weight a trace carries no signal worth keeping — the weekly
# decay sweep deletes it rather than let the table grow unbounded with
# traces too faint to ever crystallize.
MIN_TRACE_WEIGHT = 0.02


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True, slots=True)
class LatentConfig:
    """Thresholds and rates for the IL4 scoring/fold/decay pipeline.

    Defaults are the PRD IL4 values; see ``Settings`` in ``config.py`` for
    the deployment-configurable surface (``get_latent_config`` in
    ``latent_traces.py`` builds one of these from ``Settings``).
    """

    promotion_threshold: float = 0.30
    floor_ratio: float = 0.25
    crystallization_threshold: float = 0.60
    fold_rate: float = 0.5
    weekly_decay: float = 0.98
    max_traces_per_user: int = 500
    min_trace_weight: float = MIN_TRACE_WEIGHT

    @property
    def floor(self) -> float:
        """Candidates scoring below this are rejected outright (never fold)."""
        return self.floor_ratio * self.promotion_threshold


DEFAULT_LATENT_CONFIG = LatentConfig()


def score_candidate(
    *,
    importance: int | float,
    emotional_salience: float = 0.0,
    evidence_strength: float = 0.8,
) -> float:
    """Normalized candidate score `s` in [0, 1].

    ``s = clamp01(0.6 * importance/5 + 0.3 * emotional_salience + 0.1 * evidence_strength)``
    """
    return _clamp01(
        0.6 * (float(importance) / 5.0)
        + 0.3 * float(emotional_salience)
        + 0.1 * float(evidence_strength)
    )


def classify_score(score: float, config: LatentConfig = DEFAULT_LATENT_CONFIG) -> str:
    """Return ``"reject" | "fold" | "promote"`` for a normalized score.

    - ``score < floor`` -> "reject" (dropped, as an unconditional drop
      always was before IL4 introduced a scoring path at all).
    - ``floor <= score < promotion_threshold`` -> "fold" (accumulate into a
      latent trace instead of vanishing).
    - ``score >= promotion_threshold`` -> "promote" (unchanged behavior).
    """
    if score < config.floor:
        return "reject"
    if score < config.promotion_threshold:
        return "fold"
    return "promote"


def fold_weight(
    current_weight: float,
    score: float,
    config: LatentConfig = DEFAULT_LATENT_CONFIG,
) -> float:
    """Additive leaky-integrator fold: ``weight <- min(1.0, weight + fold_rate * s)``."""
    return min(1.0, current_weight + config.fold_rate * score)


def decay_weight(weight: float, config: LatentConfig = DEFAULT_LATENT_CONFIG) -> float:
    """Weekly decay leak: ``weight *= weekly_decay``."""
    return weight * config.weekly_decay


def should_crystallize(weight: float, config: LatentConfig = DEFAULT_LATENT_CONFIG) -> bool:
    return weight >= config.crystallization_threshold


def should_prune(weight: float, config: LatentConfig = DEFAULT_LATENT_CONFIG) -> bool:
    return weight < config.min_trace_weight
