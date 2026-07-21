"""IL7 — Dream cycle dynamics (pure).

The arithmetic half of the dream cycle: night-window eligibility, the
rank-normalized "coldness" weighting used to sample important-but-cold
material, weighted sampling itself, and the 25%-strength affect scaling. All
functions here are pure — no DB, no LLM, no clock reads, no module-level
randomness. Stochastic sampling takes an injected ``random.Random`` so it is
deterministic under a seed (mirrors the "inject Δt / inject the rng" purity
discipline the rest of ``inner_life`` follows).

The edge half (material gathering from the heat pool / latent traces /
transcript archive, the single extraction-model reflection call, the
``dream_journal`` write, the reduced-strength IL6 reconsolidation pass, and
raising IL3 ``dream_residue``) lives in ``dream_edge.py`` so this module can
be exercised with plain fixtures, exactly like ``affect.py`` vs ``store.py``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

TURN_DELTA_CAP = 0.15  # mirror of affect.TURN_DELTA_CAP — a normal turn's ceiling


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True, slots=True)
class DreamConfig:
    """Thresholds for eligibility, material selection, and effect strength.
    Defaults are the PRD IL7 values."""

    idle_hours_min: float = 4.0
    night_start_hour: int = 0  # local 00:00
    night_end_hour: int = 6  # local 06:00 (exclusive)
    max_dreams_per_night: int = 1
    material_k: int = 3  # important-but-cold items sampled
    latent_weight_min: float = 0.5  # latent traces at/above this weight join
    affect_scale: float = 0.25  # dream affect deltas at 25% of a normal turn
    journal_cap: int = 30  # rolling cap on dream_journal rows per user
    dream_eta: float = 0.02  # reduced-strength IL6 reconsolidation step
    # A dream is "share-worthy" (raises IL3 dream_residue) when it drew on
    # material at least this significant (importance-normalized [0,1]).
    share_significance_threshold: float = 0.6


DEFAULT_DREAM_CONFIG = DreamConfig()


@dataclass(frozen=True, slots=True)
class DreamCandidate:
    """One poolable memory item reduced to the fields dream sampling needs.
    ``ref`` is an opaque handle the edge maps back to the real row (e.g. the
    MemoryItem id) — this module never touches the DB."""

    ref: int
    importance: int  # F2 importance, 1..5
    emotional_salience: float  # [0,1]
    heat: float  # F2 heat, unbounded >= 0


def is_night_window(local_hour: int, config: DreamConfig = DEFAULT_DREAM_CONFIG) -> bool:
    """Whether ``local_hour`` (0..23, user-local) is inside the night window.
    Handles a window that wraps past midnight (start > end), though the PRD
    default 00:00–06:00 does not wrap."""
    start, end = config.night_start_hour, config.night_end_hour
    if start <= end:
        return start <= local_hour < end
    return local_hour >= start or local_hour < end


def is_dream_eligible(
    *,
    idle_hours: float,
    local_hour: int,
    dreams_tonight: int,
    config: DreamConfig = DEFAULT_DREAM_CONFIG,
) -> bool:
    """All three gates: long-idle, inside the night window, and under the
    per-night cap. Pure predicate — the edge supplies the resolved inputs."""
    return (
        idle_hours >= config.idle_hours_min
        and is_night_window(local_hour, config)
        and dreams_tonight < config.max_dreams_per_night
    )


def significance(candidate: DreamCandidate) -> float:
    """Importance-and-salience blend in [0,1] — the "important" half of
    "important but cold". Importance dominates (it is the deliberate F2
    signal); emotional salience is a secondary lift."""
    return _clamp01(0.7 * (candidate.importance / 5.0) + 0.3 * candidate.emotional_salience)


def rank_normalized(values: list[float]) -> list[float]:
    """Each value's rank position mapped to [0,1] (lowest -> 0, highest -> 1),
    ties sharing their average position. A single value maps to 0.5 (neutral —
    there is no spread to rank against). This is the bounded transform the PRD
    requires: raw F2 heat is unbounded above 1, so ``1 - heat`` can go negative
    and corrupt sampling weights; ``1 - rank_normalized(heat)`` cannot."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    order = sorted(range(n), key=lambda i: values[i])
    # Average-rank for ties: group equal values and assign the mean position.
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_pos = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_pos / (n - 1)
        i = j + 1
    return ranks


def coldness_of_pool(heats: list[float]) -> list[float]:
    """``coldness = 1 - rank_normalized(heat)`` per item — coldest (lowest
    heat) -> 1.0, hottest -> 0.0, always in [0,1]."""
    return [1.0 - r for r in rank_normalized(heats)]


def material_weights(candidates: list[DreamCandidate]) -> list[float]:
    """Per-candidate sampling weight = significance x coldness, computed over
    the whole pool (coldness is pool-relative). Zero-weight candidates (hottest
    item at significance 0) are allowed; sampling handles an all-zero pool."""
    if not candidates:
        return []
    colds = coldness_of_pool([c.heat for c in candidates])
    return [significance(c) * cold for c, cold in zip(candidates, colds, strict=True)]


def sample_material(
    candidates: list[DreamCandidate],
    rng: random.Random,
    config: DreamConfig = DEFAULT_DREAM_CONFIG,
) -> list[DreamCandidate]:
    """Sample up to ``config.material_k`` candidates weighted by
    significance x coldness, WITHOUT replacement, using the injected ``rng``
    for determinism. Falls back to uniform selection if every weight is zero
    (e.g. a pool of one maximally-hot item), so a dream still gets material."""
    pool = list(candidates)
    if not pool:
        return []
    weights = material_weights(pool)
    k = min(config.material_k, len(pool))
    chosen: list[DreamCandidate] = []
    idxs = list(range(len(pool)))
    for _ in range(k):
        w = [weights[i] for i in idxs]
        total = sum(w)
        if total <= 0.0:
            pick = rng.randrange(len(idxs))  # all-zero -> uniform
        else:
            pick = _weighted_index(w, rng.random() * total)
        chosen.append(pool[idxs.pop(pick)])
    return chosen


def _weighted_index(weights: list[float], target: float) -> int:
    """Index whose cumulative weight first exceeds ``target`` (target already
    scaled into [0, sum))."""
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if target < acc:
            return i
    return len(weights) - 1  # float slack -> last


def scale_affect_delta(
    valence_delta: float,
    arousal_delta: float,
    energy_delta: float,
    config: DreamConfig = DEFAULT_DREAM_CONFIG,
) -> tuple[float, float, float]:
    """Scale raw dream affect deltas to ``affect_scale`` (25%) of a normal
    turn and clamp each to that fraction of ``TURN_DELTA_CAP``. A dream can
    only ever nudge affect a quarter as hard as a live turn."""
    cap = TURN_DELTA_CAP * config.affect_scale
    return (
        _clamp(valence_delta * config.affect_scale, -cap, cap),
        _clamp(arousal_delta * config.affect_scale, -cap, cap),
        _clamp(energy_delta * config.affect_scale, -cap, cap),
    )


def is_share_worthy(
    selected: list[DreamCandidate], config: DreamConfig = DEFAULT_DREAM_CONFIG
) -> bool:
    """A dream is share-worthy (raises IL3 ``dream_residue``) when it drew on
    at least one item whose significance meets the threshold."""
    return any(significance(c) >= config.share_significance_threshold for c in selected)
