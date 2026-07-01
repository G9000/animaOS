from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from anima_server.config import settings


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


class RetrievalRouterDecisionSource(StrEnum):
    LLM = "llm"
    FALLBACK = "fallback"


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
    decision_source: RetrievalRouterDecisionSource = RetrievalRouterDecisionSource.FALLBACK
    confidence: float | None = None
    language: str | None = None
    semantic_rationale: str | None = None
    fallback_reason: str | None = None

    @property
    def primary_source(self) -> RetrievalSourcePlan | None:
        return self.sources[0] if self.sources else None

    @property
    def source_names(self) -> list[RetrievalSource]:
        return [source.source for source in self.sources]

    def source_for(self, source: RetrievalSource) -> RetrievalSourcePlan | None:
        return next((plan for plan in self.sources if plan.source is source), None)

    def to_trace(self) -> dict[str, object]:
        trace: dict[str, object] = {
            "route": self.route.value,
            "query": self.query,
            "rationale": self.rationale,
            "decisionSource": self.decision_source.value,
            "sources": [source.to_trace() for source in self.sources],
        }
        if self.confidence is not None:
            trace["confidence"] = self.confidence
        if self.language:
            trace["language"] = self.language
        if self.semantic_rationale:
            trace["semanticRationale"] = self.semantic_rationale
        if self.fallback_reason:
            trace["fallbackReason"] = self.fallback_reason
        return trace


def plan_retrieval(turn: str) -> RetrievalQueryPlan:
    """Build a deterministic retrieval query plan for the current user turn."""
    query = _normalize_query(turn)
    route = _classify_route(query)
    return _plan_for_route(route, query)


async def plan_retrieval_semantic(
    turn: str,
    *,
    client: Any | None = None,
) -> RetrievalQueryPlan:
    """Build an LLM-classified retrieval plan with deterministic fallback."""
    query = _normalize_query(turn)
    if not _semantic_router_enabled(client):
        return _fallback_plan(query, "semantic_router_disabled")

    try:
        from anima_server.services.agent.llm_json import call_llm_for_json

        parsed = await call_llm_for_json(
            _SEMANTIC_ROUTER_SYSTEM,
            _build_semantic_router_prompt(query),
            client=client,
        )
    except Exception:
        return _fallback_plan(query, "llm_error")

    semantic_decision = _validate_semantic_decision(parsed)
    if isinstance(semantic_decision, str):
        return _fallback_plan(query, semantic_decision)

    route, confidence, language, semantic_rationale = semantic_decision
    return _plan_for_route(
        route,
        query,
        decision_source=RetrievalRouterDecisionSource.LLM,
        confidence=confidence,
        language=language,
        semantic_rationale=semantic_rationale,
    )


def _normalize_query(turn: str) -> str:
    return re.sub(r"\s+", " ", turn.strip())


def _semantic_router_enabled(client: Any | None) -> bool:
    if client is not None:
        return True
    if settings.agent_provider == "scaffold":
        return False
    return settings.agent_retrieval_router_mode == "semantic"


def _fallback_plan(query: str, reason: str) -> RetrievalQueryPlan:
    route = _classify_route(query)
    return _plan_for_route(
        route,
        query,
        decision_source=RetrievalRouterDecisionSource.FALLBACK,
        fallback_reason=reason,
    )


SemanticDecision = tuple[RetrievalRoute, float, str | None, str | None]


def _validate_semantic_decision(parsed: object) -> SemanticDecision | str:
    if not isinstance(parsed, dict):
        return "invalid_json"

    route_raw = parsed.get("route")
    if not isinstance(route_raw, str):
        return "invalid_route"

    try:
        route = RetrievalRoute(route_raw.strip().casefold())
    except ValueError:
        return "invalid_route"

    confidence = _coerce_confidence(parsed.get("confidence"))
    if confidence is None:
        return "invalid_confidence"
    if confidence < _SEMANTIC_ROUTER_MIN_CONFIDENCE:
        return "low_confidence"

    return (
        route,
        confidence,
        _coerce_optional_text(parsed.get("language"), max_length=40),
        _coerce_optional_text(parsed.get("rationale"), max_length=240),
    )


def _coerce_confidence(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    return round(confidence, 3)


def _coerce_optional_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value.strip())
    if not text:
        return None
    return text[:max_length]


_SEMANTIC_ROUTER_MIN_CONFIDENCE = 0.55

_SEMANTIC_ROUTER_SYSTEM = """You classify one user turn for ANIMA memory retrieval.
Return only a JSON object with these keys:
{"route": string, "confidence": number from 0 to 1, "language": string, "rationale": string}

Classify the user's intent by meaning, not by English keyword matching. Handle
multilingual text, slang, typos, and code-switching. Use a lower confidence when
the route is genuinely ambiguous. Do not invent facts; only classify the route."""


def _build_semantic_router_prompt(query: str) -> str:
    route_descriptions = "\n".join(
        f'- "{route.value}": {_ROUTE_RATIONALES[route]}' for route in RetrievalRoute
    )
    return (
        "Choose exactly one retrieval route for this user turn.\n\n"
        "Routes:\n"
        f"{route_descriptions}\n\n"
        "Routing guidance:\n"
        "- Emotional distress, reassurance, rejection, anxiety, or relational pain "
        "routes to emotional_support, even when phrased in slang or another language.\n"
        "- Active ticket, PRD, project status, or what-to-do-next questions route to "
        "project_continuity unless the user asks about a real future commitment.\n"
        "- Preference-sensitive recommendations route to preference_lookup. "
        "Comparisons like 'instead of' are preferences unless the user is correcting memory.\n"
        "- Corrections to stored facts or changed personal state route to contradiction_update.\n"
        "- Learned workflows, review loops, and process memory route to procedural_skill_recall.\n"
        "- If no specialized route fits, use general_recall.\n\n"
        f"User turn JSON string: {json.dumps(query, ensure_ascii=False)}"
    )


def _classify_route(query: str) -> RetrievalRoute:
    lowered = query.casefold()

    if _has_contradiction_update_cue(lowered):
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

    if _has_project_continuity_cue(lowered) and not _has_taste_preference_cue(lowered):
        return RetrievalRoute.PROJECT_CONTINUITY

    if _has_preference_cue(lowered):
        return RetrievalRoute.PREFERENCE_LOOKUP

    if _has_project_continuity_cue(lowered):
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


def _plan_for_route(
    route: RetrievalRoute,
    query: str,
    *,
    decision_source: RetrievalRouterDecisionSource = RetrievalRouterDecisionSource.FALLBACK,
    confidence: float | None = None,
    language: str | None = None,
    semantic_rationale: str | None = None,
    fallback_reason: str | None = None,
) -> RetrievalQueryPlan:
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
        decision_source=decision_source,
        confidence=confidence,
        language=language,
        semantic_rationale=semantic_rationale,
        fallback_reason=fallback_reason,
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


def _has_contradiction_update_cue(text: str) -> bool:
    return _has_any(
        text,
        (
            "actually",
            "correction",
            "correct that",
            "not anymore",
            "no longer",
            "i joined",
            "i moved to",
            "i changed",
        ),
    )


def _has_emotional_support_cue(text: str) -> bool:
    return (
        bool(re.search(r"\bfeel(?:ing)?\s+(?!like\b)", text))
        or _has_any(
            text,
            (
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
            "tomorrow",
            "later today",
            "remind me",
            "promise",
            "promised",
            "i said i would",
            "deadline",
        ),
    ) or bool(
        re.search(
            r"\bnext\s+"
            r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
            r"week|weekend|month|year)\b",
            text,
        )
    ) or bool(re.search(r"\bdue\b", text))


def _has_relationship_cue(text: str, query: str) -> bool:
    if _has_any(text, ("connected to", "relationship")):
        return _has_named_entity_hint(query) or _has_relationship_role_query(text)
    if _has_relationship_role_query(text):
        return True
    if _has_relationship_identity_query(text):
        return True
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


def _has_relationship_identity_query(text: str) -> bool:
    direct = re.search(
        r"\bwho\s+(?:is|are)\s+(?P<target>[\w-]+(?:\s+[\w-]+){0,2})\s*\??$",
        text,
    )
    if direct and _is_bare_relationship_target(direct.group("target")):
        return True

    embedded = re.search(
        r"\bwho\s+(?P<target>[\w-]+(?:\s+[\w-]+){0,2})\s+(?:is|are)\b",
        text,
    )
    return bool(embedded and _is_bare_relationship_target(embedded.group("target")))


def _is_bare_relationship_target(target: str) -> bool:
    tokens = [token.casefold() for token in re.findall(r"\b[\w-]{2,}\b", target)]
    return bool(tokens) and tokens[0] not in {
        "a",
        "an",
        "her",
        "his",
        "my",
        "our",
        "the",
        "their",
        "your",
    }


def _has_project_continuity_cue(text: str) -> bool:
    return _has_any(
        text,
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
    )


def _has_preference_cue(text: str) -> bool:
    return _has_any(
        text,
        (
            "prefer",
            "usually like",
            "do i like",
            "favorite",
            "favourite",
            "recommend",
            "recommendation",
        ),
    )


def _has_taste_preference_cue(text: str) -> bool:
    return _has_any(
        text,
        (
            "prefer",
            "usually like",
            "do i like",
            "favorite",
            "favourite",
            "might like",
            "would like",
            "would i like",
        ),
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
