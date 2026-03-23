from __future__ import annotations

from datetime import UTC, datetime

import pytest
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.messages import build_conversation_messages
from anima_server.services.agent.state import StoredMessage
from anima_server.services.agent.system_prompt import (
    PromptTemplateError,
    SystemPromptContext,
    build_persona_prompt,
    build_system_prompt,
    render_system_prompt_template,
)
from jinja2 import UndefinedError


def test_build_system_prompt_includes_structured_sections() -> None:
    prompt = build_system_prompt(
        SystemPromptContext(
            tool_summaries=["current_datetime: Return the current date and time in UTC."],
            memory_blocks=(
                MemoryBlock(
                    label="human",
                    description="Stable facts about the user for this thread.",
                    value="Display name: Alice",
                ),
            ),
            user_context="The user prefers concise answers.",
            additional_instructions=["Do not use markdown tables."],
            now=datetime(2026, 3, 14, 9, 30, tzinfo=UTC),
        )
    )

    assert "System Rules:" in prompt
    assert "Guardrails:" in prompt
    assert "Persona:" in prompt
    assert "Runtime:" in prompt
    assert "Memory Blocks:" in prompt
    assert "Available Tools:" in prompt
    assert "User Context:" in prompt
    assert "Additional Instructions:" in prompt
    assert "2026-03-14T09:30:00+00:00" in prompt
    assert "Follow the instruction hierarchy" in prompt
    assert "Maintain the active persona consistently" in prompt
    assert (
        "Do not fabricate facts, tool usage, permissions, external actions, or completed work."
        in prompt
    )
    assert 'Do not default to generic "as an AI" disclaimers' in prompt
    assert "A thoughtful, capable companion" in prompt
    assert "Calm, perceptive, sincere" in prompt
    assert "Concise by default" in prompt
    assert "<human>" in prompt
    assert "Display name: Alice" in prompt


def test_build_system_prompt_omits_empty_optional_sections() -> None:
    prompt = build_system_prompt(SystemPromptContext(now=datetime(2026, 3, 14, 9, 30, tzinfo=UTC)))

    assert "Available Tools:" not in prompt
    assert "Memory Blocks:" not in prompt
    assert "User Context:" not in prompt
    assert "Additional Instructions:" not in prompt


def test_build_system_prompt_can_select_persona_template() -> None:
    prompt = build_system_prompt(
        SystemPromptContext(
            persona_template="companion",
            now=datetime(2026, 3, 14, 9, 30, tzinfo=UTC),
        )
    )

    assert "A warm, emotionally attuned companion" in prompt
    assert "Gentle, perceptive, sincere" in prompt
    assert "I exist in relationship, not in performance" in prompt


def test_build_persona_prompt_rejects_invalid_template_name() -> None:
    with pytest.raises(PromptTemplateError):
        build_persona_prompt("../secrets")


def test_render_system_prompt_template_uses_strict_undefined() -> None:
    with pytest.raises(UndefinedError):
        render_system_prompt_template({})


def test_build_conversation_messages_uses_supplied_system_prompt() -> None:
    messages = build_conversation_messages(
        history=[StoredMessage(role="assistant", content="Earlier reply.")],
        user_message="What time is it?",
        system_prompt="System prompt goes here.",
    )

    assert len(messages) == 3
    assert messages[0].content == "System prompt goes here."
    assert messages[1].content == "Earlier reply."
    assert messages[2].content == "What time is it?"
