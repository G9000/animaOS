"""Tests for heat-based memory scoring — F2."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from anima_server.services.agent.heat_scoring import (
    HEAT_DELTA,
    RECENCY_TAU_HOURS,
    compute_heat,
    compute_time_decay,
)


class TestComputeTimeDecay:
    def test_zero_hours(self):
        now = datetime.now(UTC)
        assert compute_time_decay(now, now) == pytest.approx(1.0)

    def test_tau_hours(self):
        now = datetime.now(UTC)
        past = now - timedelta(hours=RECENCY_TAU_HOURS)
        expected = math.exp(-1.0)
        assert compute_time_decay(past, now) == pytest.approx(expected, rel=1e-3)

    def test_48_hours_decay(self):
        now = datetime.now(UTC)
        past = now - timedelta(hours=48)
        result = compute_time_decay(past, now)
        # With tau=24: exp(-48/24) = exp(-2) ~ 0.135
        assert result < 0.25  # At least 75% decay

    def test_naive_datetimes_treated_as_utc(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        past = datetime(2026, 1, 1, 0, 0, 0)  # 12 hours ago
        result = compute_time_decay(past, now, tau_hours=24.0)
        expected = math.exp(-12.0 / 24.0)
        assert result == pytest.approx(expected, rel=1e-3)


class TestComputeHeat:
    def test_basic_formula(self):
        now = datetime.now(UTC)
        heat = compute_heat(
            access_count=5,
            interaction_depth=5,
            last_accessed_at=now,
            importance=7.0,
            now=now,
        )
        # H = 1.0*5 + 1.0*5 + 1.0*1.0 + 0.5*7 = 14.5
        assert heat == pytest.approx(14.5, rel=1e-2)

    def test_no_access(self):
        heat = compute_heat(
            access_count=0,
            interaction_depth=0,
            last_accessed_at=None,
            importance=3.0,
        )
        # H = 0 + 0 + 0 (no recency) + 0.5*3*0 = 0.0
        # importance is weighted by recency, so no recency = no importance contribution
        assert heat == pytest.approx(0.0, abs=1e-6)

    def test_frequently_accessed_beats_old(self):
        now = datetime.now(UTC)
        hot = compute_heat(
            access_count=10,
            interaction_depth=10,
            last_accessed_at=now,
            importance=3.0,
            now=now,
        )
        cold = compute_heat(
            access_count=1,
            interaction_depth=1,
            last_accessed_at=now - timedelta(days=7),
            importance=3.0,
            now=now,
        )
        assert hot > cold

    def test_heat_increases_with_each_access(self):
        now = datetime.now(UTC)
        heats = []
        for n in range(1, 6):
            h = compute_heat(
                access_count=n,
                interaction_depth=n,
                last_accessed_at=now,
                importance=3.0,
                now=now,
            )
            heats.append(h)
        # Each access should increase heat
        for i in range(1, len(heats)):
            assert heats[i] > heats[i - 1]

    def test_importance_contributes(self):
        now = datetime.now(UTC)
        low = compute_heat(
            access_count=1,
            interaction_depth=1,
            last_accessed_at=now,
            importance=1.0,
            now=now,
        )
        high = compute_heat(
            access_count=1,
            interaction_depth=1,
            last_accessed_at=now,
            importance=10.0,
            now=now,
        )
        assert high > low
        # Difference should be HEAT_DELTA * (10 - 1) = 0.5 * 9 = 4.5
        assert (high - low) == pytest.approx(HEAT_DELTA * 9.0, rel=1e-2)

    def test_rust_fast_path_skips_out_of_scale_importance(self, monkeypatch):
        from anima_server.services.agent import heat_scoring as heat_module

        now = datetime(2026, 4, 26, tzinfo=UTC)

        def _unexpected_rust_heat(**_kwargs):
            pytest.fail("out-of-scale importance should use the Python heat path")

        monkeypatch.setattr(
            heat_module.anima_core_bindings,
            "rust_compute_heat",
            _unexpected_rust_heat,
        )

        heat = heat_module.compute_heat(
            access_count=1,
            interaction_depth=1,
            last_accessed_at=now,
            importance=10.0,
            now=now,
        )

        assert heat == pytest.approx((1.0 + 1.0 + HEAT_DELTA * 10.0) + 1.0)

    def test_defaults_used_when_now_omitted(self):
        """compute_heat should work without explicit now parameter."""
        heat = compute_heat(
            access_count=3,
            interaction_depth=3,
            last_accessed_at=datetime.now(UTC),
            importance=5.0,
        )
        assert heat > 0
