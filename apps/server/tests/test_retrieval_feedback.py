from __future__ import annotations

from anima_server.services.agent.retrieval_feedback import infer_retrieval_feedback_outcomes
from anima_server.services.agent.state import (
    AgentCitation,
    AgentContextFragment,
    AgentRetrievalTrace,
)


def test_infer_retrieval_feedback_marks_used_and_unused_items() -> None:
    retrieval = AgentRetrievalTrace(
        retriever="hybrid",
        citations=(
            AgentCitation(index=1, memory_item_id=7, uri="memory://items/7"),
            AgentCitation(index=2, memory_item_id=8, uri="memory://items/8"),
        ),
        context_fragments=(
            AgentContextFragment(
                rank=1,
                memory_item_id=7,
                uri="memory://items/7",
                text="Likes cats",
            ),
            AgentContextFragment(
                rank=2,
                memory_item_id=8,
                uri="memory://items/8",
                text="Runs marathons on weekends",
            ),
        ),
    )

    outcomes = infer_retrieval_feedback_outcomes(
        retrieval=retrieval,
        response_text="You like cats.",
    )

    outcome_by_item = {outcome.memory_item_id: outcome for outcome in outcomes}
    assert outcome_by_item[7].was_used is True
    assert outcome_by_item[7].was_corrected is False
    assert outcome_by_item[7].evidence_score > 0.0
    assert outcome_by_item[8].was_used is False
    assert outcome_by_item[8].was_corrected is False
    assert outcome_by_item[8].evidence_score == 0.0


def test_infer_retrieval_feedback_marks_corrected_items() -> None:
    retrieval = AgentRetrievalTrace(
        retriever="hybrid",
        citations=(
            AgentCitation(index=1, memory_item_id=7, uri="memory://items/7"),
            AgentCitation(index=2, memory_item_id=8, uri="memory://items/8"),
        ),
        context_fragments=(
            AgentContextFragment(
                rank=1,
                memory_item_id=7,
                uri="memory://items/7",
                text="Lives in Paris",
            ),
            AgentContextFragment(
                rank=2,
                memory_item_id=8,
                uri="memory://items/8",
                text="Likes cats",
            ),
        ),
    )

    outcomes = infer_retrieval_feedback_outcomes(
        retrieval=retrieval,
        response_text="To clarify, it's Berlin, not Paris.",
    )

    outcome_by_item = {outcome.memory_item_id: outcome for outcome in outcomes}
    assert outcome_by_item[7].was_used is False
    assert outcome_by_item[7].was_corrected is True
    assert outcome_by_item[7].evidence_score > 0.0
    assert outcome_by_item[8].was_used is False
    assert outcome_by_item[8].was_corrected is False
