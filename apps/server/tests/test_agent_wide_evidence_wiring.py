from __future__ import annotations

import pytest
from anima_server.services.agent import service as agent_service
from anima_server.services.agent.evidence_retrieval import WideEvidenceResult
from anima_server.services.agent.retrieval_intent import (
    RetrievalIntent,
    RetrievalMode,
    classify_retrieval_intent,
)


@pytest.mark.asyncio
async def test_pre_turn_uses_wide_evidence_for_aggregate_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent = classify_retrieval_intent("How many model kits have I worked on or bought?")
    assert intent.mode == RetrievalMode.AGGREGATE

    fake_result = WideEvidenceResult(
        intent=intent,
        semantic_results=[
            (10, "Session date: 2023/05/29\nUser: I got a 1/72 scale B-29 bomber.", 0.9),
            (11, "Session date: 2023/05/21\nUser: I finished a Revell F-15 Eagle kit.", 0.7),
        ],
        query_embedding=[0.1, 0.2],
        total_considered=2,
    )

    async def fake_retrieve(**kwargs):
        return fake_result

    monkeypatch.setattr(
        "anima_server.services.agent.evidence_retrieval.retrieve_wide_evidence",
        fake_retrieve,
    )

    ctx = await agent_service._run_wide_evidence_retrieval(
        db=object(),
        user_id=1,
        user_message="How many model kits have I worked on or bought?",
        runtime_db=None,
    )

    assert ctx.used is True
    assert ctx.evidence_results is not None
    assert ctx.evidence_results[0][0] == 10
    assert ctx.retrieval_trace is not None
    assert ctx.retrieval_trace.retriever == "hybrid_wide_evidence"
    assert len(ctx.retrieval_trace.context_fragments) == 2
    assert ctx.retrieval_trace.stats is not None
    assert ctx.retrieval_trace.stats.returned == 2
    assert ctx.retrieval_trace.stats.triggered_by == str(RetrievalMode.AGGREGATE)
    assert ctx.query_embedding == [0.1, 0.2]


@pytest.mark.asyncio
async def test_pre_turn_skips_wide_evidence_for_direct_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct_intent = RetrievalIntent(mode=RetrievalMode.DIRECT)
    fake_result = WideEvidenceResult(intent=direct_intent)

    called = False

    async def fake_retrieve(**kwargs):
        nonlocal called
        called = True
        return fake_result

    monkeypatch.setattr(
        "anima_server.services.agent.evidence_retrieval.retrieve_wide_evidence",
        fake_retrieve,
    )

    ctx = await agent_service._run_wide_evidence_retrieval(
        db=object(),
        user_id=1,
        user_message="What is my dog's name?",
        runtime_db=None,
    )

    assert called is True
    assert ctx.used is False
    assert ctx.evidence_results is None
    assert ctx.retrieval_trace is None


@pytest.mark.asyncio
async def test_pre_turn_falls_back_when_wide_intent_returns_no_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aggregate_intent = classify_retrieval_intent(
        "How many model kits have I worked on or bought?"
    )
    fake_result = WideEvidenceResult(
        intent=aggregate_intent,
        semantic_results=[],
        query_embedding=[0.3, 0.4],
        total_considered=0,
    )

    async def fake_retrieve(**kwargs):
        return fake_result

    monkeypatch.setattr(
        "anima_server.services.agent.evidence_retrieval.retrieve_wide_evidence",
        fake_retrieve,
    )

    ctx = await agent_service._run_wide_evidence_retrieval(
        db=object(),
        user_id=1,
        user_message="How many model kits have I worked on or bought?",
        runtime_db=None,
    )

    assert ctx.used is False
    assert ctx.retrieval_trace is None
    assert ctx.query_embedding == [0.3, 0.4]
