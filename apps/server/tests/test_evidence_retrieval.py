from typing import ClassVar

import pytest
from anima_server.services.agent import evidence_retrieval
from anima_server.services.agent.evidence_retrieval import (
    compact_evidence_text,
    extract_session_date,
    rerank_evidence_texts,
)
from anima_server.services.agent.retrieval_intent import RetrievalIntent, RetrievalMode


def test_extracts_session_date_from_raw_chunk() -> None:
    text = "Session date: 2023/05/29 (Mon) 20:29\nUser: I bought a 1/72 scale B-29."

    assert extract_session_date(text) == "2023/05/29 (Mon) 20:29"


def test_compacts_to_relevant_user_lines_and_keeps_date() -> None:
    text = (
        "Session date: 2023/05/29 (Mon) 20:29\n"
        "User: I bought a 1/72 scale B-29 bomber model kit and a 1/24 scale Camaro.\n"
        "Assistant: Here are many long photo-etching tips that are not the answer."
    )

    compacted = compact_evidence_text(text, query_terms={"model", "kit", "bomber", "camaro"})

    assert "Session date: 2023/05/29" in compacted
    assert "B-29 bomber" in compacted
    assert "Camaro" in compacted
    assert "photo-etching tips" not in compacted


def test_aggregate_rerank_preserves_distinct_model_kit_evidence() -> None:
    texts = [
        "Session date: 2023/05/21\nUser: I finished a Revell F-15 Eagle kit.",
        "Session date: 2023/05/21\nAssistant: Weathering tips for models.",
        "Session date: 2023/05/27\nUser: I started a 1/16 scale German Tiger I tank diorama.",
        "Session date: 2023/05/29\nUser: I got a 1/72 scale B-29 bomber and a 1/24 scale Camaro.",
    ]
    intent = RetrievalIntent(mode=RetrievalMode.AGGREGATE, candidate_limit=50, max_evidence=3)

    ranked = rerank_evidence_texts(
        texts,
        query="How many model kits have I worked on or bought?",
        intent=intent,
    )

    joined = "\n".join(ranked)
    assert "Revell F-15" in joined
    assert "German Tiger" in joined
    assert "B-29 bomber" in joined
    assert "Camaro" in joined
    assert "Weathering tips" not in joined


def test_latest_update_rerank_prefers_newer_entity_evidence() -> None:
    texts = [
        "Session date: 2023/05/21\nUser: Rachel moved to a new apartment in the city.",
        "Session date: 2023/05/26\nUser: Rachel relocated to the suburbs.",
    ]
    intent = RetrievalIntent(mode=RetrievalMode.LATEST_UPDATE, candidate_limit=40, max_evidence=2)

    ranked = rerank_evidence_texts(
        texts,
        query="Where did Rachel move to after her recent relocation?",
        intent=intent,
    )

    assert "suburbs" in ranked[0]


@pytest.mark.asyncio
async def test_retrieve_wide_evidence_uses_intent_candidate_limit(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeItem:
        id = 10
        category = "fact"
        content = "Session date: 2023/05/29\nUser: I got a 1/72 scale B-29 bomber."

    class FakeResult:
        query_embedding: ClassVar[list[float]] = [0.1, 0.2]
        items: ClassVar[list] = [(FakeItem(), 0.9)]

    async def fake_hybrid_search(db, **kwargs):
        calls.append(kwargs)
        return FakeResult()

    monkeypatch.setattr(evidence_retrieval, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(evidence_retrieval, "df", lambda user_id, value, **kwargs: value)

    result = await evidence_retrieval.retrieve_wide_evidence(
        db=object(),
        user_id=1,
        query="How many model kits have I worked on or bought?",
        runtime_db=None,
    )

    assert result.intent.mode == RetrievalMode.AGGREGATE
    assert calls[0]["limit"] >= 40
    assert result.query_embedding == [0.1, 0.2]
    assert result.semantic_results[0][0] == 10
    assert "B-29 bomber" in result.semantic_results[0][1]


@pytest.mark.asyncio
async def test_retrieve_wide_evidence_skips_for_direct_intent(monkeypatch) -> None:
    called = False

    async def fake_hybrid_search(db, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("hybrid_search should not be called for DIRECT intent")

    monkeypatch.setattr(evidence_retrieval, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(evidence_retrieval, "df", lambda user_id, value, **kwargs: value)

    result = await evidence_retrieval.retrieve_wide_evidence(
        db=object(),
        user_id=1,
        query="What is my dog's name?",
        runtime_db=None,
    )

    assert result.intent.mode == RetrievalMode.DIRECT
    assert result.semantic_results == []
    assert result.query_embedding is None
    assert called is False
