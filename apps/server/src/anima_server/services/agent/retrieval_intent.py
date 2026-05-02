from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RetrievalMode(StrEnum):
    DIRECT = "direct"
    AGGREGATE = "aggregate"
    TEMPORAL = "temporal"
    LATEST_UPDATE = "latest_update"
    PREFERENCE = "preference"


@dataclass(frozen=True, slots=True)
class RetrievalIntent:
    mode: RetrievalMode
    candidate_limit: int = 15
    max_evidence: int = 6
    min_distinct_sessions: int = 1

    @property
    def needs_wide_evidence(self) -> bool:
        return self.mode is not RetrievalMode.DIRECT


_AGGREGATE_TRIGGERS = ("how many", "number of", "count of", "total of")
_TEMPORAL_TRIGGERS = ("first", "earlier", "before", "prior to", "previously")
_LATEST_TRIGGERS = (
    "recent",
    "latest",
    "most recent",
    "after her",
    "after his",
    "after their",
    "move to",
    "moved to",
    "current",
    "now",
    "since",
)
_PREFERENCE_TRIGGERS = (
    "recommend",
    "recommendation",
    "resources",
    "conference",
    "publication",
    "what should i",
    "any tips",
    "suggest",
)


def classify_retrieval_intent(query: str) -> RetrievalIntent:
    """Classify a user query into a retrieval mode used to size candidate pools.

    Heuristic-only; deterministic; no LLM calls.
    """

    q = (query or "").strip().lower()

    if not q:
        return RetrievalIntent(mode=RetrievalMode.DIRECT)

    if any(trigger in q for trigger in _AGGREGATE_TRIGGERS) or q.startswith("count "):
        return RetrievalIntent(
            mode=RetrievalMode.AGGREGATE,
            candidate_limit=50,
            max_evidence=10,
            min_distinct_sessions=3,
        )

    if "which" in q and any(trigger in q for trigger in _TEMPORAL_TRIGGERS):
        return RetrievalIntent(
            mode=RetrievalMode.TEMPORAL,
            candidate_limit=40,
            max_evidence=8,
            min_distinct_sessions=2,
        )

    if any(trigger in q for trigger in _LATEST_TRIGGERS):
        return RetrievalIntent(
            mode=RetrievalMode.LATEST_UPDATE,
            candidate_limit=40,
            max_evidence=8,
            min_distinct_sessions=2,
        )

    if any(trigger in q for trigger in _PREFERENCE_TRIGGERS):
        return RetrievalIntent(
            mode=RetrievalMode.PREFERENCE,
            candidate_limit=35,
            max_evidence=6,
            min_distinct_sessions=1,
        )

    return RetrievalIntent(mode=RetrievalMode.DIRECT)
