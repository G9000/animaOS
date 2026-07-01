from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RetrievalRoute(StrEnum):
    FACTUAL_RECALL = "factual_recall"
    EMOTIONAL_SUPPORT = "emotional_support"
    RELATIONSHIP_CONTEXT = "relationship_context"
    PROJECT_CONTINUITY = "project_continuity"
    PREFERENCE_LOOKUP = "preference_lookup"
    FORESIGHT_RECALL = "foresight_recall"
    CONTRADICTION_UPDATE = "contradiction_update"
    PROCEDURAL_SKILL_RECALL = "procedural_skill_recall"
    GENERAL_RECALL = "general_recall"


class RetrievalSource(StrEnum):
    USER_PROFILE = "user_profile"
    KNOWLEDGE_GRAPH = "knowledge_graph"
    MEMORY_ITEMS = "memory_items"
    EPISODES = "episodes"
    TRANSCRIPTS = "transcripts"
    FORESIGHT = "foresight"
    EXPERIENCES = "experiences"
    SKILLS = "skills"


@dataclass(frozen=True, slots=True)
class RetrievalSourcePlan:
    source: RetrievalSource
    query: str
    mode: str
    limit: int
    weight: float = 1.0
    filters: dict[str, Any] = field(default_factory=dict)
    available: bool = True
    reason: str = ""

    def to_trace(self) -> dict[str, object]:
        return {
            "source": self.source.value,
            "query": self.query,
            "mode": self.mode,
            "limit": self.limit,
            "weight": self.weight,
            "filters": dict(self.filters),
            "available": self.available,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RetrievalQueryPlan:
    route: RetrievalRoute
    query: str
    rationale: str
    sources: tuple[RetrievalSourcePlan, ...]

    @property
    def primary_source(self) -> RetrievalSourcePlan | None:
        return self.sources[0] if self.sources else None

    @property
    def source_names(self) -> list[RetrievalSource]:
        return [source.source for source in self.sources]

    def source_for(self, source: RetrievalSource) -> RetrievalSourcePlan | None:
        return next((plan for plan in self.sources if plan.source is source), None)

    def to_trace(self) -> dict[str, object]:
        return {
            "route": self.route.value,
            "query": self.query,
            "rationale": self.rationale,
            "sources": [source.to_trace() for source in self.sources],
        }


def plan_retrieval(turn: str) -> RetrievalQueryPlan:
    """Build a deterministic retrieval query plan for the current user turn."""
    query = _normalize_query(turn)
    route = _classify_route(query)
    return _plan_for_route(route, query)


def _normalize_query(turn: str) -> str:
    return re.sub(r"\s+", " ", turn.strip())


def _classify_route(query: str) -> RetrievalRoute:
    lowered = query.casefold()

    if _has_any(
        lowered,
        (
            "actually",
            "correction",
            "correct that",
            "not anymore",
            "no longer",
            "instead",
            "i joined",
            "i moved to",
            "i changed",
        ),
    ):
        return RetrievalRoute.CONTRADICTION_UPDATE

    if _has_any(
        lowered,
        (
            "how do you usually",
            "how should you handle",
            "what did you learn",
            "workflow",
            "checklist",
            "review loop",
            "release loop",
            "process for",
        ),
    ):
        return RetrievalRoute.PROCEDURAL_SKILL_RECALL

    if _has_emotional_support_cue(lowered):
        return RetrievalRoute.EMOTIONAL_SUPPORT

    if _has_foresight_cue(lowered):
        return RetrievalRoute.FORESIGHT_RECALL

    if _has_relationship_cue(lowered, query):
        return RetrievalRoute.RELATIONSHIP_CONTEXT

    if _has_any(
        lowered,
        (
            "prefer",
            "usually like",
            "do i like",
            "favorite",
            "favourite",
            "recommend",
            "recommendation",
        ),
    ):
        return RetrievalRoute.PREFERENCE_LOOKUP

    if _has_any(
        lowered,
        (
            "leave off",
            "left off",
            "current status",
            "where did we",
            "project",
            "ticket",
            "sum-",
            "prd",
            "roadmap",
            "milestone",
        ),
    ):
        return RetrievalRoute.PROJECT_CONTINUITY

    if _has_any(
        lowered,
        (
            "what was",
            "what did",
            "when did",
            "where did",
            "exact",
            "name of",
            "remember",
            "mentioned",
        ),
    ):
        return RetrievalRoute.FACTUAL_RECALL

    return RetrievalRoute.GENERAL_RECALL


def _plan_for_route(route: RetrievalRoute, query: str) -> RetrievalQueryPlan:
    sources_by_route: dict[RetrievalRoute, tuple[RetrievalSourcePlan, ...]] = {
        RetrievalRoute.FACTUAL_RECALL: (
            _memory_items(query, mode="hybrid_evidence", limit=12),
            _transcripts(query, mode="exact"),
            _episodes(query, mode="episodic", limit=6),
            _profile(query, mode="profile_lookup", limit=4),
        ),
        RetrievalRoute.EMOTIONAL_SUPPORT: (
            _profile(
                query,
                mode="profile_lookup",
                limit=8,
                filters={
                    "profile_categories": [
                        "relationships",
                        "emotional_patterns",
                        "constraints",
                    ]
                },
            ),
            _graph(query, mode="relationship_neighborhood", limit=8),
            _memory_items(
                query,
                mode="hybrid_emotional",
                limit=10,
                filters={"memory_categories": ["relationship", "goal", "fact"]},
            ),
            _episodes(query, mode="emotional_context", limit=6),
            _transcripts(query, mode="supporting_history", limit=5),
        ),
        RetrievalRoute.RELATIONSHIP_CONTEXT: (
            _graph(query, mode="relationship_neighborhood", limit=12),
            _profile(
                query,
                mode="profile_lookup",
                limit=8,
                filters={"profile_categories": ["relationships"]},
            ),
            _memory_items(
                query,
                mode="hybrid_relationship",
                limit=10,
                filters={"memory_categories": ["relationship", "fact"]},
            ),
            _episodes(query, mode="relationship_history", limit=5),
            _transcripts(query, mode="exact", limit=5),
        ),
        RetrievalRoute.PROJECT_CONTINUITY: (
            _profile(
                query,
                mode="profile_lookup",
                limit=8,
                filters={"profile_categories": ["active_projects", "work", "goals"]},
            ),
            _graph(query, mode="project_neighborhood", limit=10),
            _memory_items(
                query,
                mode="hybrid_project",
                limit=12,
                filters={"memory_categories": ["focus", "goal", "fact"]},
            ),
            _episodes(query, mode="recent_project_history", limit=6),
            _transcripts(query, mode="exact", limit=8),
        ),
        RetrievalRoute.PREFERENCE_LOOKUP: (
            _profile(
                query,
                mode="profile_lookup",
                limit=8,
                filters={"profile_categories": ["preferences", "constraints"]},
            ),
            _memory_items(
                query,
                mode="preference",
                limit=10,
                filters={"memory_categories": ["preference"]},
            ),
            _transcripts(query, mode="preference_evidence", limit=5),
            _episodes(query, mode="preference_history", limit=4),
        ),
        RetrievalRoute.FORESIGHT_RECALL: (
            _planned(RetrievalSource.FORESIGHT, query, mode="future_commitments"),
            _memory_items(
                query,
                mode="temporal",
                limit=8,
                filters={"memory_categories": ["goal", "focus", "fact"]},
            ),
            _episodes(query, mode="temporal", limit=6),
            _transcripts(query, mode="temporal", limit=8),
        ),
        RetrievalRoute.CONTRADICTION_UPDATE: (
            _profile(query, mode="current_profile_state", limit=8),
            _graph(query, mode="latest_belief", limit=8),
            _memory_items(query, mode="contradiction_scan", limit=12),
            _transcripts(query, mode="source_evidence", limit=6),
            _episodes(query, mode="change_history", limit=4),
        ),
        RetrievalRoute.PROCEDURAL_SKILL_RECALL: (
            _planned(RetrievalSource.SKILLS, query, mode="skill_lookup"),
            _planned(RetrievalSource.EXPERIENCES, query, mode="experience_lookup"),
            _memory_items(
                query,
                mode="procedural_memory",
                limit=8,
                filters={"memory_categories": ["fact", "goal", "preference"]},
            ),
            _transcripts(query, mode="workflow_evidence", limit=6),
            _episodes(query, mode="workflow_history", limit=5),
        ),
        RetrievalRoute.GENERAL_RECALL: (
            _memory_items(query, mode="hybrid", limit=12),
            _episodes(query, mode="recent_context", limit=5),
            _profile(query, mode="profile_lookup", limit=5),
        ),
    }
    return RetrievalQueryPlan(
        route=route,
        query=query,
        rationale=_ROUTE_RATIONALES[route],
        sources=sources_by_route[route],
    )


_ROUTE_RATIONALES: dict[RetrievalRoute, str] = {
    RetrievalRoute.FACTUAL_RECALL: "The user is asking for a concrete remembered fact or exact evidence.",
    RetrievalRoute.EMOTIONAL_SUPPORT: "The user needs relational or emotional continuity more than generic fact ranking.",
    RetrievalRoute.RELATIONSHIP_CONTEXT: "The user is asking how people, entities, or projects are connected.",
    RetrievalRoute.PROJECT_CONTINUITY: "The user is asking for active project state or where work left off.",
    RetrievalRoute.PREFERENCE_LOOKUP: "The user is asking for a preference-sensitive answer.",
    RetrievalRoute.FORESIGHT_RECALL: "The user is asking about future commitments or due items.",
    RetrievalRoute.CONTRADICTION_UPDATE: "The user is correcting or evolving existing memory.",
    RetrievalRoute.PROCEDURAL_SKILL_RECALL: "The user is asking for learned process or skill memory.",
    RetrievalRoute.GENERAL_RECALL: "No specialized route matched, so use general contextual recall.",
}


def _memory_items(
    query: str,
    *,
    mode: str,
    limit: int,
    filters: dict[str, Any] | None = None,
) -> RetrievalSourcePlan:
    return RetrievalSourcePlan(
        source=RetrievalSource.MEMORY_ITEMS,
        query=query,
        mode=mode,
        limit=limit,
        filters=filters or {},
        weight=1.0,
    )


def _profile(
    query: str,
    *,
    mode: str,
    limit: int,
    filters: dict[str, Any] | None = None,
) -> RetrievalSourcePlan:
    return RetrievalSourcePlan(
        source=RetrievalSource.USER_PROFILE,
        query=query,
        mode=mode,
        limit=limit,
        filters=filters or {},
        weight=0.95,
    )


def _graph(query: str, *, mode: str, limit: int) -> RetrievalSourcePlan:
    return RetrievalSourcePlan(
        source=RetrievalSource.KNOWLEDGE_GRAPH,
        query=query,
        mode=mode,
        limit=limit,
        weight=0.9,
    )


def _episodes(query: str, *, mode: str, limit: int) -> RetrievalSourcePlan:
    return RetrievalSourcePlan(
        source=RetrievalSource.EPISODES,
        query=query,
        mode=mode,
        limit=limit,
        weight=0.8,
    )


def _transcripts(query: str, *, mode: str, limit: int = 6) -> RetrievalSourcePlan:
    return RetrievalSourcePlan(
        source=RetrievalSource.TRANSCRIPTS,
        query=query,
        mode=mode,
        limit=limit,
        weight=0.75,
    )


def _planned(
    source: RetrievalSource,
    query: str,
    *,
    mode: str,
    limit: int = 8,
) -> RetrievalSourcePlan:
    return RetrievalSourcePlan(
        source=source,
        query=query,
        mode=mode,
        limit=limit,
        weight=0.7,
        available=False,
        reason="planned_source",
    )


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _has_emotional_support_cue(text: str) -> bool:
    return (
        ("feel " in text and not re.search(r"\bfeel\s+like\b", text))
        or _has_any(
            text,
            (
                "feeling",
                "overwhelmed",
                "alone",
                "rejected",
                "scared",
                "anxious",
                "sad",
                "stressed",
                "breakup",
                "grief",
                "hurt",
            ),
        )
    )


def _has_foresight_cue(text: str) -> bool:
    return _has_any(
        text,
        (
            "next ",
            "tomorrow",
            "later today",
            "remind me",
            "promise",
            "promised",
            "i said i would",
            "deadline",
        ),
    ) or bool(re.search(r"\bdue\b", text))


def _has_relationship_cue(text: str, query: str) -> bool:
    if _has_any(text, ("connected to", "relationship", "who is", "who ")):
        return _has_named_entity_hint(query) or _has_relationship_role_query(text)
    return _has_relationship_role_target(text)


def _has_relationship_role_target(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:my|our|your|their)\s+"
            r"(?:partner|friend|family|coworker|colleague)s?\b",
            text,
        )
    )


def _has_relationship_role_query(text: str) -> bool:
    return bool(
        re.search(
            r"\bwho\s+(?:is|are)\s+(?:my|our|your|their|the)\s+"
            r"(?:partner|friend|family|coworker|colleague)s?\b",
            text,
        )
    )


def _has_named_entity_hint(query: str) -> bool:
    ignored_tokens = {
        "a",
        "an",
        "and",
        "are",
        "connected",
        "connection",
        "coworker",
        "colleague",
        "did",
        "does",
        "family",
        "friend",
        "her",
        "his",
        "how",
        "is",
        "my",
        "our",
        "partner",
        "relationship",
        "the",
        "their",
        "to",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "your",
    }
    return any(
        token.casefold() not in ignored_tokens
        for token in re.findall(r"\b[\w-]{2,}\b", query)
    )
