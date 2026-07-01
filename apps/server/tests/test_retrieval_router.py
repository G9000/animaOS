import json

import pytest
from anima_server.services.agent.retrieval_router import (
    RetrievalRoute,
    RetrievalSource,
    plan_retrieval,
)
from anima_server.services.agent.state import (
    AgentRetrievalTrace,
    serialize_agent_retrieval,
)


@pytest.mark.parametrize(
    ("turn", "expected_route"),
    [
        ("What was the exact name of the Kyoto restaurant I mentioned?", RetrievalRoute.FACTUAL_RECALL),
        ("I feel overwhelmed and alone after the breakup.", RetrievalRoute.EMOTIONAL_SUPPORT),
        ("How is Maya connected to the Berlin project?", RetrievalRoute.RELATIONSHIP_CONTEXT),
        ("Where did we leave off on the AnimaOS memory router work?", RetrievalRoute.PROJECT_CONTINUITY),
        ("What coffee do I usually prefer before late coding sessions?", RetrievalRoute.PREFERENCE_LOOKUP),
        ("Remind me what I said I would do next Friday.", RetrievalRoute.FORESIGHT_RECALL),
        ("Actually I do not work at Meta anymore; I joined OpenAI.", RetrievalRoute.CONTRADICTION_UPDATE),
        ("How do you usually handle my PR review loop?", RetrievalRoute.PROCEDURAL_SKILL_RECALL),
    ],
)
def test_plan_retrieval_routes_representative_turns(
    turn: str,
    expected_route: RetrievalRoute,
) -> None:
    plan = plan_retrieval(turn)

    assert plan.route is expected_route
    assert plan.primary_source is not None
    assert plan.to_trace()["route"] == expected_route.value
    json.dumps(plan.to_trace())


def test_relationship_route_requires_named_relationship_target() -> None:
    relationship = plan_retrieval("How is Maya connected to the Berlin project?")
    project = plan_retrieval("How is the SUM-005 project going?")

    assert relationship.route is RetrievalRoute.RELATIONSHIP_CONTEXT
    assert project.route is RetrievalRoute.PROJECT_CONTINUITY


def test_relationship_route_handles_lowercase_targets() -> None:
    direct = plan_retrieval("who is maya?")
    connected = plan_retrieval("how is maya connected to berlin?")

    assert direct.route is RetrievalRoute.RELATIONSHIP_CONTEXT
    assert connected.route is RetrievalRoute.RELATIONSHIP_CONTEXT


def test_emotional_support_takes_precedence_over_generic_need_to() -> None:
    plan = plan_retrieval("I need to talk because I feel anxious about Maya.")

    assert plan.route is RetrievalRoute.EMOTIONAL_SUPPORT


def test_feel_like_preference_phrase_routes_as_preference_lookup() -> None:
    plan = plan_retrieval("I feel like Thai tonight; what do I usually like?")

    assert plan.route is RetrievalRoute.PREFERENCE_LOOKUP


def test_feeling_like_preference_phrase_routes_as_preference_lookup() -> None:
    plan = plan_retrieval("I'm feeling like Thai tonight; what do I usually like?")

    assert plan.route is RetrievalRoute.PREFERENCE_LOOKUP


@pytest.mark.parametrize(
    "turn",
    [
        "who is my partner?",
        "who is my friend?",
        "who is my family?",
    ],
)
def test_role_only_relationship_questions_route_to_relationship_context(
    turn: str,
) -> None:
    plan = plan_retrieval(turn)

    assert plan.route is RetrievalRoute.RELATIONSHIP_CONTEXT


def test_family_friendly_recommendation_routes_to_preference_lookup() -> None:
    plan = plan_retrieval("Can you recommend a family-friendly restaurant I might like?")

    assert plan.route is RetrievalRoute.PREFERENCE_LOOKUP


def test_instead_of_preference_phrase_routes_to_preference_lookup() -> None:
    plan = plan_retrieval("Can you recommend coffee instead of tea?")

    assert plan.route is RetrievalRoute.PREFERENCE_LOOKUP


@pytest.mark.parametrize(
    ("turn", "expected_route"),
    [
        ("I need to know what coffee I prefer.", RetrievalRoute.PREFERENCE_LOOKUP),
        ("I need to remember who Maya is.", RetrievalRoute.RELATIONSHIP_CONTEXT),
        ("I need to know where we left off on SUM-005.", RetrievalRoute.PROJECT_CONTINUITY),
        ("I need to remember what I said about Kyoto.", RetrievalRoute.FACTUAL_RECALL),
    ],
)
def test_generic_need_to_recall_does_not_force_foresight(
    turn: str,
    expected_route: RetrievalRoute,
) -> None:
    plan = plan_retrieval(turn)

    assert plan.route is expected_route


def test_need_to_with_future_commitment_remains_foresight() -> None:
    plan = plan_retrieval("I need to finish the SUM-005 follow-up tomorrow.")

    assert plan.route is RetrievalRoute.FORESIGHT_RECALL


def test_due_substring_in_preference_word_does_not_route_to_foresight() -> None:
    plan = plan_retrieval("Do I like fondue?")

    assert plan.route is RetrievalRoute.PREFERENCE_LOOKUP


def test_emotional_support_plan_prioritizes_relationship_and_episode_context() -> None:
    plan = plan_retrieval("I am scared Maya is pulling away and I feel rejected.")

    assert plan.route is RetrievalRoute.EMOTIONAL_SUPPORT
    assert plan.source_names[:4] == [
        RetrievalSource.USER_PROFILE,
        RetrievalSource.KNOWLEDGE_GRAPH,
        RetrievalSource.MEMORY_ITEMS,
        RetrievalSource.EPISODES,
    ]
    profile_source = plan.source_for(RetrievalSource.USER_PROFILE)
    assert profile_source is not None
    assert profile_source.filters["profile_categories"] == [
        "relationships",
        "emotional_patterns",
        "constraints",
    ]


def test_project_continuity_plan_prioritizes_active_projects() -> None:
    plan = plan_retrieval("What is the current status of the SUM-005 retrieval work?")

    assert plan.route is RetrievalRoute.PROJECT_CONTINUITY
    assert plan.primary_source.source is RetrievalSource.USER_PROFILE
    assert plan.primary_source.filters["profile_categories"] == ["active_projects", "work", "goals"]
    assert RetrievalSource.KNOWLEDGE_GRAPH in plan.source_names
    assert RetrievalSource.TRANSCRIPTS in plan.source_names


def test_project_recommendation_routes_to_project_continuity() -> None:
    plan = plan_retrieval("Can you recommend an approach for the SUM-005 PRD?")

    assert plan.route is RetrievalRoute.PROJECT_CONTINUITY


def test_next_project_step_routes_to_project_continuity() -> None:
    plan = plan_retrieval("What should we do next on SUM-005?")

    assert plan.route is RetrievalRoute.PROJECT_CONTINUITY


def test_generic_who_favorite_question_routes_to_preference_lookup() -> None:
    plan = plan_retrieval("Who did I say was my favorite author?")

    assert plan.route is RetrievalRoute.PREFERENCE_LOOKUP


def test_future_and_procedural_sources_are_explicit_until_storage_exists() -> None:
    foresight = plan_retrieval("What did I promise to send Alex next Tuesday?")
    procedural = plan_retrieval("What did you learn about handling my release checklist?")

    foresight_source = foresight.source_for(RetrievalSource.FORESIGHT)
    experience_source = procedural.source_for(RetrievalSource.EXPERIENCES)
    skill_source = procedural.source_for(RetrievalSource.SKILLS)

    assert foresight.route is RetrievalRoute.FORESIGHT_RECALL
    assert foresight_source is not None
    assert foresight_source.available is False
    assert foresight_source.reason == "planned_source"
    assert RetrievalSource.TRANSCRIPTS in foresight.source_names

    assert procedural.route is RetrievalRoute.PROCEDURAL_SKILL_RECALL
    assert experience_source is not None
    assert skill_source is not None
    assert experience_source.available is False
    assert skill_source.available is False
    assert experience_source.reason == "planned_source"
    assert skill_source.reason == "planned_source"


def test_agent_retrieval_trace_serializes_router_plan() -> None:
    plan = plan_retrieval("What exact thing did I say about Kyoto?")
    trace = AgentRetrievalTrace(
        retriever="hybrid",
        query_plan=plan.to_trace(),
    )

    payload = serialize_agent_retrieval(trace)

    assert payload is not None
    assert payload["queryPlan"]["route"] == RetrievalRoute.FACTUAL_RECALL.value
    assert payload["queryPlan"]["sources"][0]["source"] == RetrievalSource.MEMORY_ITEMS.value
    json.dumps(payload["queryPlan"])
