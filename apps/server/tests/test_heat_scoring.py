"""Tests for heat-based memory scoring — F2."""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest
from anima_server.services.agent.heat_scoring import (
    HEAT_ALPHA,
    HEAT_BETA,
    HEAT_GAMMA,
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
        # H = 0 + 0 + 0 (no last_accessed) + 0.5*3 = 1.5
        assert heat == pytest.approx(1.5, rel=1e-2)

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

    def test_defaults_used_when_now_omitted(self):
        """compute_heat should work without explicit now parameter."""
        heat = compute_heat(
            access_count=3,
            interaction_depth=3,
            last_accessed_at=None,
            importance=5.0,
        )
        assert heat > 0
