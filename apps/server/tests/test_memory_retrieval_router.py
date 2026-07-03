from __future__ import annotations

from anima_server.services.agent import evidence_retrieval
from anima_server.services.agent.evidence_retrieval import RetrievalMode, WideEvidenceResult
from anima_server.services.agent.tool_context import (
    ToolContext,
    clear_tool_context,
    set_tool_context,
)
from anima_server.services.agent.tools import search_long_memory
from anima_server.services.memory.retrieval_router import (
    RetrievalIntent,
    RetrievalLane,
    RetrievalTraceItem,
    build_query_plan,
    classify_retrieval_intent,
    mode_for_plan,
)

ROUTER_FIXTURES: tuple[tuple[str, RetrievalIntent], ...] = (
    ("What is my brother's name?", RetrievalIntent.FACTUAL_RECALL),
    ("I feel awful about what happened with Maya, help me understand it.", RetrievalIntent.EMOTIONAL_SUPPORT),
    ("What context do you have about my relationship with Sam?", RetrievalIntent.RELATIONSHIP_CONTEXT),
    ("Where did we leave off on the SUM-005 router work?", RetrievalIntent.PROJECT_CONTINUITY),
    ("What coffee style do I prefer?", RetrievalIntent.PREFERENCE_LOOKUP),
    ("What was my latest update about the memory PR?", RetrievalIntent.TEMPORAL_LATEST),
    ("How many model kits have I bought?", RetrievalIntent.AGGREGATE_COUNT),
    ("What should you remind me about tomorrow?", RetrievalIntent.FORESIGHT),
    ("How did we fix the Alembic migration issue last time?", RetrievalIntent.PROCEDURAL_SKILL),
    ("I no longer work at Acme, update that memory.", RetrievalIntent.CONTRADICTION_UPDATE),
)


def test_router_handles_mixed_intent_project_precedence() -> None:
    assert classify_retrieval_intent("what's the plan for SUM-005?") is RetrievalIntent.PROJECT_CONTINUITY
    assert classify_retrieval_intent("what do I prefer for this project?") is RetrievalIntent.PREFERENCE_LOOKUP


def test_router_fixture_accuracy_covers_supported_intents() -> None:
    correct = 0
    for query, expected in ROUTER_FIXTURES:
        if classify_retrieval_intent(query) is expected:
            correct += 1

    accuracy = correct / len(ROUTER_FIXTURES)
    assert accuracy >= 0.90


def test_query_plan_lane_composition_for_core_intents() -> None:
    expectations = {
        RetrievalIntent.EMOTIONAL_SUPPORT: {
            RetrievalLane.PROFILE_CLAIMS,
            RetrievalLane.GRAPH,
            RetrievalLane.EPISODES,
            RetrievalLane.TRANSCRIPTS,
        },
        RetrievalIntent.FACTUAL_RECALL: {
            RetrievalLane.PROFILE_CLAIMS,
            RetrievalLane.GRAPH,
            RetrievalLane.MEMORY_ITEMS,
            RetrievalLane.EPISODES,
        },
        RetrievalIntent.PROJECT_CONTINUITY: {
            RetrievalLane.PROFILE_CLAIMS,
            RetrievalLane.MEMORY_ITEMS,
            RetrievalLane.EPISODES,
            RetrievalLane.FORESIGHT,
            RetrievalLane.EXPERIENCES,
        },
        RetrievalIntent.PREFERENCE_LOOKUP: {
            RetrievalLane.PROFILE_CLAIMS,
            RetrievalLane.MEMORY_ITEMS,
            RetrievalLane.GRAPH,
        },
        RetrievalIntent.FORESIGHT: {
            RetrievalLane.FORESIGHT,
            RetrievalLane.PROFILE_CLAIMS,
            RetrievalLane.MEMORY_ITEMS,
            RetrievalLane.EPISODES,
        },
        RetrievalIntent.PROCEDURAL_SKILL: {
            RetrievalLane.SKILLS,
            RetrievalLane.EXPERIENCES,
            RetrievalLane.TRANSCRIPTS,
        },
        RetrievalIntent.CONTRADICTION_UPDATE: {
            RetrievalLane.PROFILE_CLAIMS,
            RetrievalLane.GRAPH,
            RetrievalLane.MEMORY_ITEMS,
            RetrievalLane.TRANSCRIPTS,
        },
        RetrievalIntent.TEMPORAL_LATEST: {
            RetrievalLane.MEMORY_ITEMS,
            RetrievalLane.EPISODES,
            RetrievalLane.TRANSCRIPTS,
        },
        RetrievalIntent.AGGREGATE_COUNT: {
            RetrievalLane.MEMORY_ITEMS,
            RetrievalLane.EPISODES,
            RetrievalLane.TRANSCRIPTS,
        },
    }

    for intent, expected_lanes in expectations.items():
        plan = build_query_plan("representative query", intent=intent)
        assert set(plan.lanes) >= expected_lanes
        assert plan.route_label == intent.value
        assert plan.route_reason


def test_query_plan_maps_to_existing_wide_evidence_modes() -> None:
    assert mode_for_plan(build_query_plan("latest update", RetrievalIntent.TEMPORAL_LATEST)) == "latest_update"
    assert mode_for_plan(build_query_plan("how many times", RetrievalIntent.AGGREGATE_COUNT)) == "aggregate"
    assert mode_for_plan(build_query_plan("what do I prefer", RetrievalIntent.PREFERENCE_LOOKUP)) == "preference"
    assert mode_for_plan(build_query_plan("when did I change that", RetrievalIntent.CONTRADICTION_UPDATE)) == "temporal"
    assert mode_for_plan(build_query_plan("what is my brother's name", RetrievalIntent.FACTUAL_RECALL)) == "aggregate"


def test_trace_item_serialization_names_route_weights_truthfully() -> None:
    trace = RetrievalTraceItem(
        lane=RetrievalLane.GRAPH,
        route_weight=0.84,
        weight_breakdown={"graph": 0.7, "lexical": 0.2, "salience": 0.1},
        evidence_ids=["relation:42", "memory:7"],
        route_reason="relationship context needs graph neighbors",
    )

    assert trace.to_dict() == {
        "lane": "graph",
        "route_weight": 0.84,
        "weight_breakdown": {"graph": 0.7, "lexical": 0.2, "salience": 0.1},
        "evidence_ids": ["relation:42", "memory:7"],
        "route_reason": "relationship context needs graph neighbors",
    }


def test_search_long_memory_auto_mode_routes_to_existing_evidence_mode(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_retrieve_wide_evidence(**kwargs: object) -> WideEvidenceResult:
        calls.append(kwargs)
        return WideEvidenceResult(
            mode=kwargs["mode"],
            semantic_results=[(21, "Session date: 2026-07-03\nUser: The router PR changed.", 0.91)],
            total_considered=1,
        )

    monkeypatch.setattr(
        evidence_retrieval,
        "retrieve_wide_evidence",
        fake_retrieve_wide_evidence,
    )
    set_tool_context(
        ToolContext(
            db=object(),
            runtime_db=object(),
            user_id=7,
            thread_id=3,
        )
    )
    try:
        result = search_long_memory(
            query="What was my latest update about the memory PR?",
            mode="auto",
        )
    finally:
        clear_tool_context()

    assert calls[0]["mode"] == RetrievalMode.LATEST_UPDATE
    assert "Route: temporal_latest -> latest_update" in result
    assert "latest update" in result
    assert "router PR changed" in result


def test_search_long_memory_none_or_blank_mode_behaves_like_auto(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_retrieve_wide_evidence(**kwargs: object) -> WideEvidenceResult:
        calls.append(kwargs)
        return WideEvidenceResult(
            mode=kwargs["mode"],
            semantic_results=[(31, "Session date: 2026-07-03\nUser: SUM-005 route plan.", 0.8)],
            total_considered=1,
        )

    monkeypatch.setattr(
        evidence_retrieval,
        "retrieve_wide_evidence",
        fake_retrieve_wide_evidence,
    )
    set_tool_context(
        ToolContext(
            db=object(),
            runtime_db=object(),
            user_id=7,
            thread_id=3,
        )
    )
    try:
        none_result = search_long_memory("what's the plan for SUM-005?", mode=None)  # type: ignore[arg-type]
        blank_result = search_long_memory("what's the plan for SUM-005?", mode=" ")
    finally:
        clear_tool_context()

    assert [call["mode"] for call in calls] == [
        RetrievalMode.LATEST_UPDATE,
        RetrievalMode.LATEST_UPDATE,
    ]
    assert "Route: project_continuity -> latest_update" in none_result
    assert "Route: project_continuity -> latest_update" in blank_result


def test_search_long_memory_unknown_non_string_mode_returns_validation_message() -> None:
    set_tool_context(
        ToolContext(
            db=object(),
            runtime_db=object(),
            user_id=7,
            thread_id=3,
        )
    )
    try:
        result = search_long_memory(query="What changed?", mode=123)  # type: ignore[arg-type]
    finally:
        clear_tool_context()

    assert "Unknown long-memory search mode: 123" in result
    assert "auto" in result
