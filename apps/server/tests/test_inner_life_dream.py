"""IL7 dream-cycle pure-math tests (services/agent/inner_life/dream.py).

Edge/integration behaviour (material gathering, the extraction LLM call, the
dream_journal write, reconsolidation, dream_residue) is covered separately;
this file exercises only the pure functions with plain fixtures.
"""

from __future__ import annotations

import random

import pytest
from anima_server.services.agent.inner_life.dream import (
    DEFAULT_DREAM_CONFIG,
    DreamCandidate,
    DreamConfig,
    coldness_of_pool,
    is_dream_eligible,
    is_night_window,
    is_share_worthy,
    material_weights,
    rank_normalized,
    sample_material,
    scale_affect_delta,
    significance,
)

TURN_DELTA_CAP = 0.15


def _cand(ref: int, importance: int = 3, salience: float = 0.5, heat: float = 0.1) -> DreamCandidate:
    return DreamCandidate(ref=ref, importance=importance, emotional_salience=salience, heat=heat)


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hour,expected",
    [(0, True), (3, True), (5, True), (6, False), (7, False), (23, False), (12, False)],
)
def test_night_window_default_midnight_to_six(hour: int, expected: bool) -> None:
    assert is_night_window(hour) is expected


def test_night_window_wraps_past_midnight() -> None:
    cfg = DreamConfig(night_start_hour=22, night_end_hour=4)
    assert is_night_window(23, cfg) is True
    assert is_night_window(2, cfg) is True
    assert is_night_window(12, cfg) is False


def test_eligible_only_when_all_three_gates_pass() -> None:
    assert is_dream_eligible(idle_hours=5.0, local_hour=2, dreams_tonight=0) is True
    # idle too short
    assert is_dream_eligible(idle_hours=3.9, local_hour=2, dreams_tonight=0) is False
    # outside night window
    assert is_dream_eligible(idle_hours=5.0, local_hour=9, dreams_tonight=0) is False
    # already dreamed tonight (cap 1)
    assert is_dream_eligible(idle_hours=5.0, local_hour=2, dreams_tonight=1) is False


def test_eligible_boundary_idle_exactly_four_hours() -> None:
    assert is_dream_eligible(idle_hours=4.0, local_hour=1, dreams_tonight=0) is True


# --------------------------------------------------------------------------
# Rank-normalized coldness (the PRD's bounded transform)
# --------------------------------------------------------------------------


def test_rank_normalized_orders_low_to_high() -> None:
    # heats 5,1,3 -> ranks: 1->0.0, 3->0.5, 5->1.0
    assert rank_normalized([5.0, 1.0, 3.0]) == [1.0, 0.0, 0.5]


def test_rank_normalized_single_is_neutral() -> None:
    assert rank_normalized([42.0]) == [0.5]


def test_rank_normalized_empty() -> None:
    assert rank_normalized([]) == []


def test_rank_normalized_ties_share_average_position() -> None:
    # two equal lowest, one high: [1,1,9] -> positions 0,1 avg 0.5 -> /2 = 0.25 each; 9 -> 1.0
    assert rank_normalized([1.0, 1.0, 9.0]) == [0.25, 0.25, 1.0]


def test_coldness_never_negative_even_with_unbounded_heat() -> None:
    # Raw heat can far exceed 1; 1 - heat would go negative, but coldness must not.
    colds = coldness_of_pool([0.01, 5.0, 87.3])
    assert all(0.0 <= c <= 1.0 for c in colds)
    assert colds[0] == 1.0  # coldest (lowest heat)
    assert colds[2] == 0.0  # hottest


# --------------------------------------------------------------------------
# Significance & weights
# --------------------------------------------------------------------------


def test_significance_importance_dominates_but_salience_lifts() -> None:
    assert significance(_cand(1, importance=5, salience=1.0)) == pytest.approx(1.0)
    assert significance(_cand(1, importance=1, salience=0.0)) == pytest.approx(0.14)
    hi_sal = significance(_cand(1, importance=3, salience=1.0))
    lo_sal = significance(_cand(1, importance=3, salience=0.0))
    assert hi_sal > lo_sal


def test_material_weights_are_significance_times_coldness() -> None:
    # cold+important should outweigh hot+important and cold+trivial.
    cands = [
        _cand(1, importance=5, salience=0.5, heat=0.01),  # important & cold
        _cand(2, importance=5, salience=0.5, heat=90.0),  # important & hot
        _cand(3, importance=1, salience=0.0, heat=0.01),  # trivial & cold
    ]
    w = material_weights(cands)
    assert w[0] > w[1]  # coldness beats heat at equal significance
    assert w[0] > w[2]  # significance beats triviality at equal coldness
    assert w[1] == pytest.approx(0.0)  # hottest -> coldness 0 -> weight 0


# --------------------------------------------------------------------------
# Weighted sampling
# --------------------------------------------------------------------------


def test_sample_is_deterministic_under_a_seed() -> None:
    cands = [_cand(i, importance=(i % 5) + 1, heat=float(i)) for i in range(10)]
    a = sample_material(cands, random.Random(1234))
    b = sample_material(cands, random.Random(1234))
    assert [c.ref for c in a] == [c.ref for c in b]


def test_sample_returns_k_distinct_items() -> None:
    cands = [_cand(i, heat=float(i)) for i in range(10)]
    picked = sample_material(cands, random.Random(7))
    assert len(picked) == DEFAULT_DREAM_CONFIG.material_k
    assert len({c.ref for c in picked}) == len(picked)  # without replacement


def test_sample_caps_at_pool_size() -> None:
    cands = [_cand(1, heat=0.1), _cand(2, heat=0.2)]
    picked = sample_material(cands, random.Random(1))
    assert len(picked) == 2


def test_sample_empty_pool() -> None:
    assert sample_material([], random.Random(1)) == []


def test_sample_all_zero_weight_falls_back_to_uniform() -> None:
    # All identical & hottest-tie -> coldness 0.5 each actually; force zero via importance 0-ish.
    # A single-heat pool: coldness neutral, but make significance 0 so weights are 0.
    cands = [_cand(i, importance=0, salience=0.0, heat=5.0) for i in range(4)]
    # importance 0 -> significance 0 -> all weights 0 -> uniform fallback, still returns k.
    picked = sample_material(cands, random.Random(3))
    assert len(picked) == DEFAULT_DREAM_CONFIG.material_k


# --------------------------------------------------------------------------
# Affect scaling (25% of a normal turn) & share-worthiness
# --------------------------------------------------------------------------


def test_affect_deltas_scaled_to_quarter_strength() -> None:
    v, a, e = scale_affect_delta(0.1, -0.08, 0.04)
    assert v == pytest.approx(0.025)  # 0.1 * 0.25
    assert a == pytest.approx(-0.02)
    assert e == pytest.approx(0.01)


def test_affect_deltas_clamped_to_quarter_of_the_turn_cap() -> None:
    cap = TURN_DELTA_CAP * 0.25  # 0.0375
    v, a, e = scale_affect_delta(10.0, -10.0, 10.0)
    assert v == pytest.approx(cap)
    assert a == pytest.approx(-cap)
    assert e == pytest.approx(cap)


def test_share_worthy_requires_significant_material() -> None:
    assert is_share_worthy([_cand(1, importance=5, salience=1.0)]) is True
    assert is_share_worthy([_cand(1, importance=1, salience=0.0)]) is False
    assert is_share_worthy([]) is False
