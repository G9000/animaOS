"""Tests for the creation ceremony agent — 3-phase scaffold + response parsing."""

from __future__ import annotations

import pytest

from anima_server.services.creation_agent import (
    CREATION_COMPLETE_MARKER,
    CreationTurnResult,
    _parse_response,
    _scaffold_turn,
    handle_creation_turn,
)


# --------------------------------------------------------------------------- #
# Scaffold turn — Phase 0: greeting
# --------------------------------------------------------------------------- #


def test_scaffold_turn_phase_0_greeting() -> None:
    """No user messages yet → asks for a name."""
    result = _scaffold_turn([])
    assert result.done is False
    assert "call me" in result.message.lower()
    assert result.soul_data is None


# --------------------------------------------------------------------------- #
# Scaffold turn — Phase 1: naming
# --------------------------------------------------------------------------- #


def test_scaffold_turn_phase_1_naming() -> None:
    messages = [
        {"role": "assistant", "content": "What would you like to call me?"},
        {"role": "user", "content": "Nova"},
    ]
    result = _scaffold_turn(messages)
    assert result.done is False
    assert "Nova" in result.message
    assert "companion" in result.message or "advisor" in result.message


def test_scaffold_turn_phase_1_empty_name_defaults_to_anima() -> None:
    messages = [
        {"role": "user", "content": "   "},
    ]
    result = _scaffold_turn(messages)
    assert "Anima" in result.message


# --------------------------------------------------------------------------- #
# Scaffold turn — Phase 2: relationship
# --------------------------------------------------------------------------- #


def test_scaffold_turn_phase_2_relationship() -> None:
    messages = [
        {"role": "user", "content": "Nova"},
        {"role": "user", "content": "advisor"},
    ]
    result = _scaffold_turn(messages)
    assert result.done is False
    assert "advisor" in result.message
    assert "speak" in result.message.lower() or "how" in result.message.lower()


# --------------------------------------------------------------------------- #
# Scaffold turn — Phase 3: completion
# --------------------------------------------------------------------------- #


def test_scaffold_turn_phase_3_completion() -> None:
    messages = [
        {"role": "user", "content": "Nova"},
        {"role": "user", "content": "companion"},
        {"role": "user", "content": "warm and casual"},
    ]
    result = _scaffold_turn(messages)
    assert result.done is True
    assert result.soul_data is not None
    assert result.soul_data["agentName"] == "Nova"
    assert result.soul_data["relationship"] == "companion"
    assert result.soul_data["style"] == "warm and casual"
    assert "Nova" in result.message


def test_scaffold_turn_phase_3_extra_turns() -> None:
    """More than 3 user messages still triggers completion."""
    messages = [
        {"role": "user", "content": "Nova"},
        {"role": "user", "content": "companion"},
        {"role": "user", "content": "casual"},
        {"role": "user", "content": "extra"},
    ]
    result = _scaffold_turn(messages)
    assert result.done is True
    assert result.soul_data is not None


# --------------------------------------------------------------------------- #
# _parse_response
# --------------------------------------------------------------------------- #


def test_parse_response_no_marker() -> None:
    result = _parse_response("Just a normal response.")
    assert result.done is False
    assert result.message == "Just a normal response."
    assert result.soul_data is None


def test_parse_response_with_marker_and_json() -> None:
    text = (
        'Wonderful! I am ready.\n'
        f'{CREATION_COMPLETE_MARKER}'
        '{"agentName": "Nova", "relationship": "companion", "style": "warm"}'
    )
    result = _parse_response(text)
    assert result.done is True
    assert result.message == "Wonderful! I am ready."
    assert result.soul_data == {
        "agentName": "Nova",
        "relationship": "companion",
        "style": "warm",
    }


def test_parse_response_with_marker_no_json() -> None:
    text = f"Done!{CREATION_COMPLETE_MARKER}"
    result = _parse_response(text)
    assert result.done is True
    assert result.message == "Done!"
    assert result.soul_data is None


def test_parse_response_with_marker_invalid_json() -> None:
    text = f"Ready!{CREATION_COMPLETE_MARKER}{{bad json}}"
    result = _parse_response(text)
    assert result.done is True
    assert result.message == "Ready!"
    assert result.soul_data is None


# --------------------------------------------------------------------------- #
# handle_creation_turn — async wrapper
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_handle_creation_turn_delegates_to_scaffold() -> None:
    """handle_creation_turn delegates to _scaffold_turn."""
    result = await handle_creation_turn([], owner_name="Alice")
    assert isinstance(result, CreationTurnResult)
    assert result.done is False
    assert "call me" in result.message.lower()


@pytest.mark.asyncio
async def test_handle_creation_turn_full_ceremony() -> None:
    """Full 3-phase ceremony through the async entry point."""
    # Phase 0
    r0 = await handle_creation_turn([], owner_name="Alice")
    assert r0.done is False

    # Phase 1
    msgs = [{"role": "user", "content": "Atlas"}]
    r1 = await handle_creation_turn(msgs, owner_name="Alice")
    assert r1.done is False
    assert "Atlas" in r1.message

    # Phase 2
    msgs.append({"role": "assistant", "content": r1.message})
    msgs.append({"role": "user", "content": "advisor"})
    r2 = await handle_creation_turn(msgs, owner_name="Alice")
    assert r2.done is False

    # Phase 3
    msgs.append({"role": "assistant", "content": r2.message})
    msgs.append({"role": "user", "content": "direct and focused"})
    r3 = await handle_creation_turn(msgs, owner_name="Alice")
    assert r3.done is True
    assert r3.soul_data is not None
    assert r3.soul_data["agentName"] == "Atlas"
    assert r3.soul_data["relationship"] == "advisor"
    assert r3.soul_data["style"] == "direct and focused"
