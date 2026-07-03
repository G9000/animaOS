from __future__ import annotations


def test_memory_domain_exports_stable_string_contracts() -> None:
    from anima_server.services.memory import domain

    assert domain.TemporalRecordStatus.ACTIVE == "active"
    assert domain.TemporalRecordStatus.SUPERSEDED == "superseded"
    assert domain.TemporalRecordStatus.RETRACTED == "retracted"
    assert domain.ForesightStatus.CANCELLED == "cancelled"
    assert domain.MemoryEndpointKind.USER == "user"
    assert domain.MemoryEndpointKind.PERSON == "person"
    assert domain.MemoryEndpointKind.EXTERNAL == "external"
    assert domain.MemoryClass.IDENTITY == "identity"
    assert domain.StabilityClass.EVOLVING == "evolving"
    assert domain.DecayClass.EPHEMERAL == "ephemeral"

    assert "active" in domain.ACTIVE_TEMPORAL_STATUSES
    assert "superseded" in domain.HISTORICAL_TEMPORAL_STATUSES
    assert "cancelled" in domain.TERMINAL_FORESIGHT_STATUSES
    assert "organization" in domain.VALID_MEMORY_ENDPOINT_KINDS

    breakdown = domain.RecallScoreBreakdown(lexical=0.25, vector=0.75)
    assert breakdown.lexical == 0.25
    assert breakdown.vector == 0.75


def test_memory_salience_facade_delegates_existing_agent_implementation() -> None:
    from anima_server.services.agent import memory_salience as agent_salience
    from anima_server.services.memory import salience

    assert salience.MemorySalience is agent_salience.MemorySalience
    assert salience.MEMORY_CLASS_IDENTITY == agent_salience.MEMORY_CLASS_IDENTITY
    assert salience.VALID_DECAY_CLASSES == agent_salience.VALID_DECAY_CLASSES
    assert salience.normalize_salience_payload is agent_salience.normalize_salience_payload

    normalized = salience.normalize_salience_payload(
        {"memory_class": "identity", "decay_class": "anchored"},
        content="The user is Leo.",
        category="fact",
        importance=5,
    )

    assert normalized["memory_class"] == "identity"
    assert normalized["decay_class"] == "anchored"


def test_memory_retrieval_facade_delegates_existing_agent_implementation() -> None:
    from anima_server.services.agent import retrieval_backends as agent_retrieval
    from anima_server.services.memory import retrieval

    assert retrieval.MemoryRetrievalDocument is agent_retrieval.MemoryRetrievalDocument
    assert retrieval.MemoryRetrievalHit is agent_retrieval.MemoryRetrievalHit
    assert retrieval.NativeMemoryRetrievalBackend is agent_retrieval.NativeMemoryRetrievalBackend
    assert retrieval.get_memory_retrieval_backend is agent_retrieval.get_memory_retrieval_backend


def test_memory_retrieval_router_exports_stable_contracts() -> None:
    from anima_server.services.memory import retrieval_router

    assert retrieval_router.RetrievalIntent.FACTUAL_RECALL == "factual_recall"
    assert retrieval_router.RetrievalLane.PROFILE_CLAIMS == "profile_claims"
    assert retrieval_router.RetrievalLane.SKILLS == "skills"
    assert retrieval_router.build_query_plan("what do I prefer?").route_label == "preference_lookup"


def test_memory_temporal_helpers_normalize_stable_status_contracts() -> None:
    from anima_server.services.memory import temporal

    assert temporal.normalize_temporal_status(" SUPERSEDED ") == "superseded"
    assert temporal.normalize_temporal_status("unknown") == "active"
    assert temporal.is_current_status("active")
    assert not temporal.is_current_status("superseded")
    assert temporal.is_historical_status("superseded")
    assert temporal.is_terminal_foresight_status("cancelled")
