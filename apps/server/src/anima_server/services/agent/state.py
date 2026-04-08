from __future__ import annotations

from dataclasses import dataclass, field

from anima_server.services.agent.prompt_budget import PromptBudgetTrace
from anima_server.services.agent.runtime_types import StepTrace, ToolCall


RETRIEVAL_CONTENT_KEY = "retrieval"


@dataclass(frozen=True, slots=True)
class StoredMessage:
    role: str
    content: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class AgentCitation:
    index: int
    memory_item_id: int
    uri: str
    score: float | None = None
    category: str | None = None


@dataclass(frozen=True, slots=True)
class AgentContextFragment:
    rank: int
    memory_item_id: int
    uri: str
    text: str
    score: float | None = None
    category: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRetrievalStats:
    retrieval_ms: float | None = None
    total_considered: int = 0
    returned: int = 0
    cutoff_index: int = 0
    cutoff_score: float | None = None
    top_score: float | None = None
    cutoff_ratio: float | None = None
    triggered_by: str = ""


@dataclass(frozen=True, slots=True)
class AgentRetrievalTrace:
    retriever: str
    citations: tuple[AgentCitation, ...] = field(default_factory=tuple)
    context_fragments: tuple[AgentContextFragment, ...] = field(default_factory=tuple)
    stats: AgentRetrievalStats | None = None


def serialize_agent_retrieval(
    retrieval: AgentRetrievalTrace | None,
) -> dict[str, object] | None:
    if retrieval is None:
        return None

    citations = [
        {
            "index": citation.index,
            "memoryItemId": citation.memory_item_id,
            "uri": citation.uri,
            "score": citation.score,
            "category": citation.category,
        }
        for citation in retrieval.citations
    ]
    context_fragments = [
        {
            "rank": fragment.rank,
            "memoryItemId": fragment.memory_item_id,
            "uri": fragment.uri,
            "text": fragment.text,
            "score": fragment.score,
            "category": fragment.category,
        }
        for fragment in retrieval.context_fragments
    ]

    stats_payload: dict[str, object] | None = None
    if retrieval.stats is not None:
        stats_payload = {
            "retrievalMs": retrieval.stats.retrieval_ms,
            "totalConsidered": retrieval.stats.total_considered,
            "returned": retrieval.stats.returned,
            "cutoffIndex": retrieval.stats.cutoff_index,
            "cutoffScore": retrieval.stats.cutoff_score,
            "topScore": retrieval.stats.top_score,
            "cutoffRatio": retrieval.stats.cutoff_ratio,
            "triggeredBy": retrieval.stats.triggered_by,
        }

    return {
        "retriever": retrieval.retriever,
        "citations": citations,
        "contextFragments": context_fragments,
        "stats": stats_payload,
    }


def attach_serialized_retrieval(
    content_json: dict[str, object] | None,
    retrieval: dict[str, object] | None,
) -> dict[str, object] | None:
    if retrieval is None:
        return content_json

    payload = dict(content_json or {})
    payload[RETRIEVAL_CONTENT_KEY] = retrieval
    return payload


def extract_stored_retrieval(
    content_json: dict[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(content_json, dict):
        return None

    retrieval = content_json.get(RETRIEVAL_CONTENT_KEY)
    return retrieval if isinstance(retrieval, dict) else None


@dataclass(slots=True)
class AgentResult:
    response: str
    model: str
    provider: str
    stop_reason: str | None = None
    tools_used: list[str] = field(default_factory=list)
    step_traces: list[StepTrace] = field(default_factory=list)
    prompt_budget: PromptBudgetTrace | None = None
    retrieval: AgentRetrievalTrace | None = None
