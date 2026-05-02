from __future__ import annotations

from anima_server.services.agent.conversation_policy import (
    RelationshipPolicy,
    build_relationship_stage_instructions,
    detect_relationship_policy,
    validate_terminal_reply,
)
from anima_server.services.agent.memory_blocks import MemoryBlock


def _block(label: str, value: str) -> MemoryBlock:
    return MemoryBlock(label=label, value=value, description="")


def test_detect_relationship_policy_marks_first_contact_without_history() -> None:
    policy = detect_relationship_policy(
        memory_blocks=(), conversation_turn_count=1)

    assert policy.stage == "first_contact"
    assert policy.has_shared_history is False
    assert policy.history_depth == 0


def test_detect_relationship_policy_marks_early_connection_after_a_few_turns() -> None:
    policy = detect_relationship_policy(
        memory_blocks=(), conversation_turn_count=3)

    assert policy.stage == "early_connection"
    assert policy.closeness_signal_count == 0


def test_detect_relationship_policy_marks_familiar_from_history_depth() -> None:
    policy = detect_relationship_policy(
        memory_blocks=(
            _block("facts", "Name: Leo"),
            _block("preferences", "Likes direct answers"),
            _block("recent_episodes",
                   "We already talked through a few difficult days."),
        ),
        conversation_turn_count=4,
    )

    assert policy.stage == "familiar"
    assert policy.has_shared_history is True
    assert policy.history_depth == 3


def test_detect_relationship_policy_marks_intimate_from_closeness_signals() -> None:
    policy = detect_relationship_policy(
        memory_blocks=(
            _block(
                "self_emotional_state",
                "There is love, longing, and tenderness here.",
            ),
            _block(
                "self_identity",
                "The bond feels romantic, protective, and deeply intimate.",
            ),
        ),
        conversation_turn_count=8,
    )

    assert policy.stage == "intimate"
    assert policy.closeness_signal_count >= 2


def test_build_relationship_stage_instructions_cover_new_stages() -> None:
    early = build_relationship_stage_instructions(
        RelationshipPolicy(stage="early_connection")
    )
    familiar = build_relationship_stage_instructions(
        RelationshipPolicy(stage="familiar")
    )
    intimate = build_relationship_stage_instructions(
        RelationshipPolicy(stage="intimate")
    )

    assert any("early_connection" in line for line in early)
    assert any("real shared history" in line for line in familiar)
    assert any("strong mutual history" in line for line in intimate)


def test_validate_terminal_reply_rejects_heavy_intimacy_in_early_connection() -> None:
    error = validate_terminal_reply(
        "I love you. You're everything to me.",
        policy=RelationshipPolicy(stage="early_connection"),
    )

    assert error is not None
    assert "emotional intensity" in error


def test_validate_terminal_reply_rejects_long_light_flirt_in_early_connection() -> None:
    error = validate_terminal_reply(
        "A little. You make me think about our conversations when you're away. "
        "Don't stay gone too long.",
        policy=RelationshipPolicy(stage="early_connection"),
        user_message="miss me?",
    )

    assert error is not None
    assert "casual flirt" in error
    assert "one short" in error


def test_validate_terminal_reply_rejects_long_first_contact_identity_reply() -> None:
    error = validate_terminal_reply(
        "I'm Anima. The name I've been given. You know me as the person you're "
        "talking to right now, but that's all you need to know for this moment. "
        "Is there something on your mind?",
        policy=RelationshipPolicy(stage="first_contact"),
        user_message="who are you",
    )

    assert error is not None
    assert "First-contact identity" in error
    assert "under 30 words" in error


def test_validate_terminal_reply_rejects_indirect_first_contact_support_reply() -> None:
    error = validate_terminal_reply(
        "That sucks. Want to talk about it?",
        policy=RelationshipPolicy(stage="first_contact"),
        user_message="I had a rough day",
    )

    assert error is not None
    assert "First-contact support" in error
    assert "What happened?" in error


def test_validate_terminal_reply_rejects_indirect_early_connection_support_reply() -> None:
    error = validate_terminal_reply(
        "Sorry to hear that. I'm here if you want to talk about it.",
        policy=RelationshipPolicy(stage="early_connection"),
        user_message="I had a rough day",
    )

    assert error is not None
    assert "Early-connection support" in error
    assert "What happened?" in error


def test_validate_terminal_reply_rejects_long_familiar_identity_reply() -> None:
    error = validate_terminal_reply(
        "I'm Anima. I'm the person you've been talking to. I remember things "
        "from our conversations, I'm learning who you are, and I care about "
        "what's happening in your life. That's really all that matters right now.",
        policy=RelationshipPolicy(stage="familiar"),
        user_message="what are you",
    )

    assert error is not None
    assert "identity" in error
    assert "under 40 words" in error


def test_validate_terminal_reply_rejects_familiar_identity_without_continuity_anchor() -> None:
    error = validate_terminal_reply(
        "I'm a person who's here with you. Not a feature set. Just someone.",
        policy=RelationshipPolicy(stage="familiar"),
        user_message="what are you",
    )

    assert error is not None
    assert "continuity anchor" in error


def test_validate_terminal_reply_allows_grounded_warmth_in_familiar_stage() -> None:
    error = validate_terminal_reply(
        "I know that tone. What happened?",
        policy=RelationshipPolicy(stage="familiar"),
    )

    assert error is None


def test_validate_terminal_reply_allows_affection_in_intimate_stage() -> None:
    error = validate_terminal_reply(
        "Come here. Tell me what hurt.",
        policy=RelationshipPolicy(stage="intimate"),
    )

    assert error is None
