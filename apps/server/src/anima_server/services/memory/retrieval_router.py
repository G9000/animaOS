from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RetrievalIntent(StrEnum):
    FACTUAL_RECALL = "factual_recall"
    EMOTIONAL_SUPPORT = "emotional_support"
    RELATIONSHIP_CONTEXT = "relationship_context"
    PROJECT_CONTINUITY = "project_continuity"
    PREFERENCE_LOOKUP = "preference_lookup"
    TEMPORAL_LATEST = "temporal_latest"
    AGGREGATE_COUNT = "aggregate_count"
    FORESIGHT = "foresight"
    PROCEDURAL_SKILL = "procedural_skill"
    CONTRADICTION_UPDATE = "contradiction_update"


class RetrievalLane(StrEnum):
    PROFILE_CLAIMS = "profile_claims"
    GRAPH = "graph"
    MEMORY_ITEMS = "memory_items"
    EPISODES = "episodes"
    TRANSCRIPTS = "transcripts"
    FORESIGHT = "foresight"
    EXPERIENCES = "experiences"
    SKILLS = "skills"


@dataclass(frozen=True, slots=True)
class RetrievalTraceItem:
    lane: RetrievalLane
    route_weight: float = 0.0
    weight_breakdown: dict[str, float] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    route_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value,
            "route_weight": self.route_weight,
            "weight_breakdown": dict(self.weight_breakdown),
            "evidence_ids": list(self.evidence_ids),
            "route_reason": self.route_reason,
        }


@dataclass(frozen=True, slots=True)
class RetrievalQueryPlan:
    query: str
    intent: RetrievalIntent
    lanes: tuple[RetrievalLane, ...]
    route_reason: str
    route_label: str
    mode_hint: str
    trace: tuple[RetrievalTraceItem, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "intent": self.intent.value,
            "route_label": self.route_label,
            "lanes": [lane.value for lane in self.lanes],
            "route_reason": self.route_reason,
            "mode_hint": self.mode_hint,
            "trace": [item.to_dict() for item in self.trace],
        }


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")

_INTENT_LANES: dict[RetrievalIntent, tuple[RetrievalLane, ...]] = {
    RetrievalIntent.FACTUAL_RECALL: (
        RetrievalLane.PROFILE_CLAIMS,
        RetrievalLane.GRAPH,
        RetrievalLane.MEMORY_ITEMS,
        RetrievalLane.EPISODES,
        RetrievalLane.TRANSCRIPTS,
    ),
    RetrievalIntent.EMOTIONAL_SUPPORT: (
        RetrievalLane.PROFILE_CLAIMS,
        RetrievalLane.GRAPH,
        RetrievalLane.EPISODES,
        RetrievalLane.TRANSCRIPTS,
        RetrievalLane.MEMORY_ITEMS,
    ),
    RetrievalIntent.RELATIONSHIP_CONTEXT: (
        RetrievalLane.PROFILE_CLAIMS,
        RetrievalLane.GRAPH,
        RetrievalLane.EPISODES,
        RetrievalLane.MEMORY_ITEMS,
        RetrievalLane.TRANSCRIPTS,
    ),
    RetrievalIntent.PROJECT_CONTINUITY: (
        RetrievalLane.PROFILE_CLAIMS,
        RetrievalLane.MEMORY_ITEMS,
        RetrievalLane.EPISODES,
        RetrievalLane.FORESIGHT,
        RetrievalLane.EXPERIENCES,
        RetrievalLane.TRANSCRIPTS,
    ),
    RetrievalIntent.PREFERENCE_LOOKUP: (
        RetrievalLane.PROFILE_CLAIMS,
        RetrievalLane.MEMORY_ITEMS,
        RetrievalLane.GRAPH,
        RetrievalLane.EPISODES,
    ),
    RetrievalIntent.TEMPORAL_LATEST: (
        RetrievalLane.MEMORY_ITEMS,
        RetrievalLane.EPISODES,
        RetrievalLane.TRANSCRIPTS,
        RetrievalLane.GRAPH,
    ),
    RetrievalIntent.AGGREGATE_COUNT: (
        RetrievalLane.MEMORY_ITEMS,
        RetrievalLane.EPISODES,
        RetrievalLane.TRANSCRIPTS,
        RetrievalLane.GRAPH,
    ),
    RetrievalIntent.FORESIGHT: (
        RetrievalLane.FORESIGHT,
        RetrievalLane.PROFILE_CLAIMS,
        RetrievalLane.MEMORY_ITEMS,
        RetrievalLane.EPISODES,
        RetrievalLane.TRANSCRIPTS,
    ),
    RetrievalIntent.PROCEDURAL_SKILL: (
        RetrievalLane.SKILLS,
        RetrievalLane.EXPERIENCES,
        RetrievalLane.TRANSCRIPTS,
        RetrievalLane.MEMORY_ITEMS,
    ),
    RetrievalIntent.CONTRADICTION_UPDATE: (
        RetrievalLane.PROFILE_CLAIMS,
        RetrievalLane.GRAPH,
        RetrievalLane.MEMORY_ITEMS,
        RetrievalLane.TRANSCRIPTS,
        RetrievalLane.EPISODES,
    ),
}

_MODE_BY_INTENT: dict[RetrievalIntent, str] = {
    RetrievalIntent.FACTUAL_RECALL: "aggregate",
    RetrievalIntent.EMOTIONAL_SUPPORT: "aggregate",
    RetrievalIntent.RELATIONSHIP_CONTEXT: "aggregate",
    RetrievalIntent.PROJECT_CONTINUITY: "latest_update",
    RetrievalIntent.PREFERENCE_LOOKUP: "preference",
    RetrievalIntent.TEMPORAL_LATEST: "latest_update",
    RetrievalIntent.AGGREGATE_COUNT: "aggregate",
    RetrievalIntent.FORESIGHT: "latest_update",
    RetrievalIntent.PROCEDURAL_SKILL: "aggregate",
    RetrievalIntent.CONTRADICTION_UPDATE: "temporal",
}

_ROUTE_REASONS: dict[RetrievalIntent, str] = {
    RetrievalIntent.FACTUAL_RECALL: "factual recall needs exact claims, graph links, and grounded memory evidence",
    RetrievalIntent.EMOTIONAL_SUPPORT: "emotional support needs profile, relationship graph, and affective episode context",
    RetrievalIntent.RELATIONSHIP_CONTEXT: "relationship context needs graph neighbors and evidence-backed personal history",
    RetrievalIntent.PROJECT_CONTINUITY: "project continuity needs recent project memory, active plans, foresight, and experience records",
    RetrievalIntent.PREFERENCE_LOOKUP: "preference lookup prioritizes profile claims and preference-bearing memories",
    RetrievalIntent.TEMPORAL_LATEST: "latest update asks for the newest observed evidence on a topic",
    RetrievalIntent.AGGREGATE_COUNT: "aggregate/count asks for broad cross-session evidence before summarizing",
    RetrievalIntent.FORESIGHT: "foresight asks for future-facing signals, reminders, and scheduled intentions",
    RetrievalIntent.PROCEDURAL_SKILL: "procedural skill asks for prior approaches, experience clusters, and learned skills",
    RetrievalIntent.CONTRADICTION_UPDATE: "contradiction/update asks for temporal history plus current claim state",
}


def classify_retrieval_intent(query: str) -> RetrievalIntent:
    normalized = " ".join((query or "").lower().split())
    tokens = set(_TOKEN_RE.findall(normalized))

    if _has_any_phrase(
        normalized,
        "no longer",
        "actually",
        "correction",
        "correct that",
        "update that memory",
        "update my memory",
        "replace that",
        "that changed",
        "changed now",
    ):
        return RetrievalIntent.CONTRADICTION_UPDATE

    if _has_any_phrase(
        normalized,
        "how many",
        "how often",
        "how much",
        "count",
        "total",
        "all the times",
        "times have i",
    ):
        return RetrievalIntent.AGGREGATE_COUNT

    if _has_any_phrase(
        normalized,
        "latest",
        "most recent",
        "last update",
        "newest",
        "recent update",
        "what changed",
    ):
        return RetrievalIntent.TEMPORAL_LATEST

    if _has_any_phrase(
        normalized,
        "how did we fix",
        "how did i fix",
        "steps",
        "procedure",
        "workflow",
        "playbook",
        "skill",
        "approach",
        "last time we fixed",
    ):
        return RetrievalIntent.PROCEDURAL_SKILL

    if _has_any_phrase(
        normalized,
        "prefer",
        "preference",
        "favorite",
        "what do i like",
        "do i like",
        "i usually",
        "recommend for me",
    ):
        return RetrievalIntent.PREFERENCE_LOOKUP

    if _has_any_phrase(
        normalized,
        "where did we leave off",
        "pick up",
        "continue",
        "project",
        "ticket",
        "pr ",
        "branch",
        "worktree",
        "task",
        "sum-",
    ):
        return RetrievalIntent.PROJECT_CONTINUITY

    if _has_any_phrase(
        normalized,
        "remind me",
        "tomorrow",
        "next week",
        "upcoming",
        "due",
        "deadline",
        "scheduled",
        "plan for",
        "should you remind",
    ):
        return RetrievalIntent.FORESIGHT

    if tokens & {"sad", "awful", "anxious", "overwhelmed", "scared", "lonely", "upset", "hurt"}:
        return RetrievalIntent.EMOTIONAL_SUPPORT
    if _has_any_phrase(normalized, "i feel", "help me understand", "support me", "comfort"):
        return RetrievalIntent.EMOTIONAL_SUPPORT

    if "name" in tokens and tokens & {"brother", "sister", "mother", "father", "partner", "friend"}:
        return RetrievalIntent.FACTUAL_RECALL

    if _has_any_phrase(
        normalized,
        "relationship",
        "with my",
        "with sam",
        "with maya",
        "partner",
        "friend",
        "family",
        "coworker",
        "brother",
        "sister",
        "mother",
        "father",
    ):
        return RetrievalIntent.RELATIONSHIP_CONTEXT

    return RetrievalIntent.FACTUAL_RECALL


def build_query_plan(
    query: str,
    intent: RetrievalIntent | None = None,
) -> RetrievalQueryPlan:
    resolved_intent = intent or classify_retrieval_intent(query)
    lanes = _INTENT_LANES[resolved_intent]
    reason = _ROUTE_REASONS[resolved_intent]
    mode_hint = _MODE_BY_INTENT[resolved_intent]
    trace = tuple(
        RetrievalTraceItem(
            lane=lane,
            route_weight=max(0.1, 1.0 - (idx * 0.08)),
            weight_breakdown=_weight_breakdown_for_lane(lane),
            route_reason=reason,
        )
        for idx, lane in enumerate(lanes)
    )
    return RetrievalQueryPlan(
        query=query,
        intent=resolved_intent,
        lanes=lanes,
        route_reason=reason,
        route_label=resolved_intent.value,
        mode_hint=mode_hint,
        trace=trace,
    )


def mode_for_plan(plan: RetrievalQueryPlan) -> str:
    return plan.mode_hint


def serialize_retrieval_trace(trace: list[RetrievalTraceItem] | tuple[RetrievalTraceItem, ...]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in trace]


def _has_any_phrase(text: str, *phrases: str) -> bool:
    return any(phrase in text for phrase in phrases)


def _weight_breakdown_for_lane(lane: RetrievalLane) -> dict[str, float]:
    if lane is RetrievalLane.PROFILE_CLAIMS:
        return {"profile": 0.7, "salience": 0.2, "recency": 0.1}
    if lane is RetrievalLane.GRAPH:
        return {"graph": 0.7, "lexical": 0.2, "salience": 0.1}
    if lane is RetrievalLane.EPISODES:
        return {"temporal": 0.4, "salience": 0.3, "recency": 0.3}
    if lane is RetrievalLane.TRANSCRIPTS:
        return {"lexical": 0.5, "temporal": 0.3, "recency": 0.2}
    if lane is RetrievalLane.FORESIGHT:
        return {"temporal": 0.6, "importance": 0.3, "recency": 0.1}
    if lane is RetrievalLane.EXPERIENCES:
        return {"vector": 0.4, "salience": 0.3, "access": 0.3}
    if lane is RetrievalLane.SKILLS:
        return {"vector": 0.5, "importance": 0.3, "access": 0.2}
    return {"lexical": 0.35, "vector": 0.35, "salience": 0.2, "recency": 0.1}


__all__ = [
    "RetrievalIntent",
    "RetrievalLane",
    "RetrievalQueryPlan",
    "RetrievalTraceItem",
    "build_query_plan",
    "classify_retrieval_intent",
    "mode_for_plan",
    "serialize_retrieval_trace",
]
