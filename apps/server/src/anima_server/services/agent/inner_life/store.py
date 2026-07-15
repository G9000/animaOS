"""Persistence for the affect state vector (IL1): the only side-effecting edge.

Runtime-tier only (`RuntimeBase` / PostgreSQL) — affect is rebuildable state
and must never reach the SQLCipher soul store or vault export/import.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.config import settings
from anima_server.models.runtime_consciousness import AffectStateRow
from anima_server.services.agent.inner_life.affect import (
    DEFAULT_AFFECT_CONFIG,
    AffectConfig,
    AffectState,
)


def get_affect_config() -> AffectConfig:
    """Build an `AffectConfig` from `Settings`, overriding taus only."""
    return AffectConfig(
        tau_valence_hours=settings.inner_life_tau_valence_hours,
        tau_arousal_hours=settings.inner_life_tau_arousal_hours,
        tau_energy_hours=settings.inner_life_tau_energy_hours,
    )


def _seed_state(config: AffectConfig) -> AffectState:
    return AffectState(
        valence=config.baseline_valence,
        arousal=config.circadian_midline,
        energy=config.baseline_energy,
        updated_at=datetime.now(UTC),
        arousal_baseline_shift=0.0,
        high_arousal_hours=0.0,
    )


def get_affect_state(
    runtime_db: Session | None,
    *,
    user_id: int,
    config: AffectConfig = DEFAULT_AFFECT_CONFIG,
) -> AffectState:
    """Load the persisted affect vector, seeding a default row on first read.

    A missing runtime session (e.g. PostgreSQL unavailable) returns the
    default state without erroring — affect is best-effort ambient state,
    not a hard dependency of the turn pipeline.
    """
    if runtime_db is None:
        return _seed_state(config)

    row = runtime_db.scalar(
        select(AffectStateRow).where(AffectStateRow.user_id == user_id)
    )
    if row is None:
        seed = _seed_state(config)
        row = AffectStateRow(
            user_id=user_id,
            valence=seed.valence,
            arousal=seed.arousal,
            energy=seed.energy,
            arousal_baseline_shift=seed.arousal_baseline_shift,
            high_arousal_hours=seed.high_arousal_hours,
            updated_at=seed.updated_at,
        )
        runtime_db.add(row)
        runtime_db.flush()
        return seed

    return AffectState(
        valence=row.valence,
        arousal=row.arousal,
        energy=row.energy,
        updated_at=row.updated_at,
        arousal_baseline_shift=row.arousal_baseline_shift,
        high_arousal_hours=row.high_arousal_hours,
    )


def save_affect_state(
    runtime_db: Session | None,
    *,
    user_id: int,
    state: AffectState,
) -> None:
    """Persist the affect vector, creating the row on first write."""
    if runtime_db is None:
        return

    row = runtime_db.scalar(
        select(AffectStateRow).where(AffectStateRow.user_id == user_id)
    )
    if row is None:
        row = AffectStateRow(user_id=user_id)
        runtime_db.add(row)

    row.valence = state.valence
    row.arousal = state.arousal
    row.energy = state.energy
    row.arousal_baseline_shift = state.arousal_baseline_shift
    row.high_arousal_hours = state.high_arousal_hours
    row.updated_at = state.updated_at
    runtime_db.flush()
