from __future__ import annotations

from anima_server.services.agent import evidence_retrieval
from anima_server.services.agent.evidence_retrieval import RetrievalMode, WideEvidenceResult
from anima_server.services.agent.tool_context import (
    ToolContext,
    clear_tool_context,
    set_tool_context,
)
from anima_server.services.agent.tools import get_core_tools, search_long_memory


def test_search_long_memory_aggregates_distinct_user_lines(
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    async def fake_retrieve_wide_evidence(**kwargs):
        calls.append(kwargs)
        return WideEvidenceResult(
            mode=RetrievalMode.AGGREGATE,
            semantic_results=[
                (
                    10,
                    "Session date: 2023/05/29\nUser: I got a 1/72 scale B-29 bomber.",
                    0.9,
                ),
                (
                    11,
                    "Session date: 2023/05/21\nUser: I finished a Revell F-15 Eagle kit.",
                    0.7,
                ),
                (
                    12,
                    "Session date: 2023/05/27\nUser: I started a German Tiger tank diorama.",
                    0.6,
                ),
            ],
            total_considered=3,
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
            query="How many model kits have I worked on or bought?",
            mode="aggregate",
        )
    finally:
        clear_tool_context()

    assert calls[0]["user_id"] == 7
    assert calls[0]["query"] == "How many model kits have I worked on or bought?"
    assert calls[0]["mode"] == RetrievalMode.AGGREGATE
    assert "2023/05/29" in result
    assert "2023/05/21" in result
    assert "2023/05/27" in result
    assert "B-29 bomber" in result
    assert "Revell F-15" in result
    assert "German Tiger" in result
    assert "Weathering tips" not in result


def test_search_long_memory_rejects_unknown_mode() -> None:
    set_tool_context(
        ToolContext(
            db=object(),
            runtime_db=object(),
            user_id=7,
            thread_id=3,
        )
    )
    try:
        result = search_long_memory(query="What changed?", mode="unknown")
    finally:
        clear_tool_context()

    assert "Unknown long-memory search mode" in result
    assert "aggregate" in result


def test_search_long_memory_registered_as_core_tool() -> None:
    tool_names = [getattr(tool, "name", None) or tool.__name__ for tool in get_core_tools()]

    assert "search_long_memory" in tool_names
