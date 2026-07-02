from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest


def test_salience_parser_clamps_and_defaults_identity_decay() -> None:
    from anima_server.services.agent.memory_salience import normalize_salience_payload

    salience = normalize_salience_payload(
        {
            "memory_class": "identity",
            "emotional_salience": 2.5,
            "stability": "stable",
            "relationship_proximity": -1,
            "evidence_strength": 0.95,
        },
        content="Name is Leo",
        category="fact",
        importance=5,
    )

    assert salience["memory_class"] == "identity"
    assert salience["decay_class"] == "anchored"
    assert salience["emotional_salience"] == 1.0
    assert salience["relationship_proximity"] == 0.0
    assert salience["evidence_strength"] == 0.95


def test_salience_decay_keeps_identity_hotter_than_casual_observation() -> None:
    from anima_server.services.agent.heat_scoring import compute_heat, importance_heat_floor

    now = datetime(2026, 7, 1, tzinfo=UTC)
    created_at = now - timedelta(days=30)

    default_floor = compute_heat(
        access_count=0,
        interaction_depth=0,
        last_accessed_at=None,
        importance=3,
        created_at=None,
    )

    identity_heat = compute_heat(
        access_count=0,
        interaction_depth=0,
        last_accessed_at=None,
        importance=5,
        created_at=created_at,
        now=now,
        decay_class="anchored",
        emotional_salience=0.8,
        evidence_strength=0.95,
    )
    casual_heat = compute_heat(
        access_count=0,
        interaction_depth=0,
        last_accessed_at=None,
        importance=2,
        created_at=created_at,
        now=now,
        decay_class="fast",
        emotional_salience=0.0,
        evidence_strength=0.7,
    )

    assert default_floor == pytest.approx(importance_heat_floor(3))
    assert identity_heat > casual_heat


def test_item_heat_preserves_explicit_zero_evidence_strength() -> None:
    from anima_server.services.agent.heat_scoring import compute_heat_for_item

    class Item:
        importance = 3
        reference_count = 0
        last_referenced_at = None
        created_at = None
        superseded_by = None
        decay_class = "standard"
        emotional_salience = 0.0
        relationship_proximity = 0.0
        evidence_strength: float | None = 0.0

    low_evidence = Item()
    neutral_evidence = Item()
    neutral_evidence.evidence_strength = 0.8

    assert compute_heat_for_item(low_evidence) < compute_heat_for_item(neutral_evidence)


def test_repeated_low_grade_emotional_signals_accumulate_salience() -> None:
    from anima_server.services.agent.memory_salience import merge_salience

    first = {
        "memory_class": "emotional_pattern",
        "emotional_salience": 0.25,
        "stability_class": "evolving",
        "decay_class": "standard",
        "relationship_proximity": 0.0,
        "evidence_strength": 0.6,
    }
    second = {
        "memory_class": "emotional_pattern",
        "emotional_salience": 0.3,
        "stability_class": "evolving",
        "decay_class": "standard",
        "relationship_proximity": 0.0,
        "evidence_strength": 0.6,
    }

    merged = merge_salience(first, second)

    assert merged.emotional_salience > first["emotional_salience"]
    assert merged.evidence_strength > first["evidence_strength"]


def test_salience_reinforcement_recomputes_persisted_heat() -> None:
    from anima_server.services.agent.memory_salience import merge_salience_into_item

    class Item:
        memory_class = "casual"
        emotional_salience = 0.0
        stability_class = "temporary"
        decay_class = "fast"
        relationship_proximity = 0.0
        evidence_strength = 0.5
        importance = 1
        reference_count = 0
        last_referenced_at = None
        created_at = datetime(2026, 1, 1, tzinfo=UTC)
        superseded_by = None
        heat = 0.001

    item = Item()

    merge_salience_into_item(
        item,
        {
            "memory_class": "identity",
            "emotional_salience": 1.0,
            "stability_class": "stable",
            "decay_class": "anchored",
            "relationship_proximity": 0.0,
            "evidence_strength": 1.0,
        },
    )

    assert item.heat > 0.001


def test_soft_preference_drift_uses_structured_salience_signal() -> None:
    from anima_server.services.agent.memory_salience import detect_soft_evolution

    evolution = detect_soft_evolution(
        category="preference",
        existing_salience={
            "memory_class": "casual",
            "emotional_salience": 0.1,
            "stability_class": "stable",
            "decay_class": "standard",
            "relationship_proximity": 0.0,
            "evidence_strength": 0.8,
        },
        incoming_salience={
            "memory_class": "casual",
            "emotional_salience": 0.1,
            "stability_class": "evolving",
            "decay_class": "standard",
            "relationship_proximity": 0.0,
            "evidence_strength": 0.8,
            "salience_source": "explicit",
            "salience_signal_fields": ["stability_class", "evidence_strength"],
        },
    )

    assert evolution is not None
    assert evolution.kind == "preference_drift"


def test_soft_evolution_ignores_inferred_language_defaults() -> None:
    from anima_server.services.agent.memory_salience import detect_soft_evolution

    assert (
        detect_soft_evolution(
            category="preference",
            existing_salience=None,
            incoming_salience={
                "memory_class": "casual",
                "emotional_salience": 0.1,
                "stability_class": "evolving",
                "decay_class": "standard",
                "relationship_proximity": 0.0,
                "evidence_strength": 0.8,
                "salience_source": "inferred",
                "salience_signal_fields": [],
            },
        )
        is None
    )


def test_standard_heat_can_still_use_rust_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    from anima_server.services import anima_core_bindings
    from anima_server.services.agent import heat_scoring

    now = datetime(2026, 7, 1, tzinfo=UTC)
    last_accessed = now - timedelta(seconds=10)

    monkeypatch.setattr(
        anima_core_bindings,
        "rust_compute_heat",
        lambda **kwargs: 8.0,
    )

    assert heat_scoring.compute_heat(
        access_count=1,
        interaction_depth=1,
        last_accessed_at=last_accessed,
        importance=3.0,
        now=now,
    ) == 8.0
