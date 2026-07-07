"""ARH-006: Anthropic prompt caching with a byte-stable prefix.

The system prompt used to be one string with a second-precision timestamp
near the top — even with cache_control, the prefix changed every turn and
the full rules/guardrails/persona budget was re-billed uncached on every
request.
"""

from __future__ import annotations

from datetime import UTC, datetime

from anima_server.services.agent.anthropic_client import AnthropicChatClient
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.messages import (
    HumanMessage,
    SystemMessage,
    make_system_message,
)
from anima_server.services.agent.system_prompt import (
    SystemPromptContext,
    build_system_prompt,
    build_system_prompt_parts,
)


def _context(
    *,
    now: datetime,
    fragments: tuple[MemoryBlock, ...] = (),
    instructions: tuple[str, ...] = (),
) -> SystemPromptContext:
    return SystemPromptContext(
        persona_content="I am warm and curious.",
        memory_blocks=(
            MemoryBlock(label="human", value="Prefers direct answers."),
            *fragments,
        ),
        dynamic_identity="I am finding my voice.",
        additional_instructions=instructions,
        now=now,
    )


# --------------------------------------------------------------------------- #
# Stable prefix invariants
# --------------------------------------------------------------------------- #


class TestStablePrefix:
    def test_stable_section_is_byte_identical_across_turns(self) -> None:
        """Different retrieved memories, different time, different stage
        instructions — the cacheable prefix must not move by a byte."""
        first = build_system_prompt_parts(
            _context(
                now=datetime(2026, 7, 7, 12, 0, 17, tzinfo=UTC),
                fragments=(
                    MemoryBlock(
                        label="retrieved_memories",
                        value="- Likes green tea (relevance: 0.87)",
                    ),
                ),
                instructions=("Stay curious.",),
            )
        )
        second = build_system_prompt_parts(
            _context(
                now=datetime(2026, 7, 8, 9, 41, 3, tzinfo=UTC),
                fragments=(
                    MemoryBlock(
                        label="retrieved_memories",
                        value="- Planning a trip to Kyoto (relevance: 0.61)",
                    ),
                ),
                instructions=("Be more familiar now.",),
            )
        )
        assert first.stable == second.stable
        assert first.volatile != second.volatile

    def test_no_timestamp_in_stable_section(self) -> None:
        parts = build_system_prompt_parts(
            _context(now=datetime(2026, 7, 7, 12, 0, 17, tzinfo=UTC))
        )
        assert "Current UTC time" not in parts.stable
        assert "2026-07-07" not in parts.stable

    def test_volatile_timestamp_is_minute_rounded(self) -> None:
        parts_a = build_system_prompt_parts(
            _context(now=datetime(2026, 7, 7, 12, 30, 5, tzinfo=UTC))
        )
        parts_b = build_system_prompt_parts(
            _context(now=datetime(2026, 7, 7, 12, 30, 58, tzinfo=UTC))
        )
        # Same minute, different seconds → identical volatile text.
        assert parts_a.volatile == parts_b.volatile
        assert "2026-07-07T12:30:00" in parts_a.volatile

    def test_full_prompt_starts_with_stable(self) -> None:
        parts = build_system_prompt_parts(
            _context(now=datetime(2026, 7, 7, 12, 0, tzinfo=UTC))
        )
        assert parts.full[: parts.stable_prefix_chars] == parts.stable
        assert parts.volatile in parts.full

    def test_build_system_prompt_matches_parts_full(self) -> None:
        context = _context(now=datetime(2026, 7, 7, 12, 0, tzinfo=UTC))
        assert build_system_prompt(context) == build_system_prompt_parts(context).full

    def test_volatile_carries_memory_blocks_and_instructions(self) -> None:
        parts = build_system_prompt_parts(
            _context(
                now=datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
                instructions=("Stay curious.",),
            )
        )
        assert "Prefers direct answers." in parts.volatile
        assert "Stay curious." in parts.volatile
        # Persona and identity stay in the stable prefix.
        assert "I am warm and curious." in parts.stable
        assert "I am finding my voice." in parts.stable


# --------------------------------------------------------------------------- #
# Anthropic payload: cache_control placement
# --------------------------------------------------------------------------- #


def _payload_for(messages: list) -> dict:
    client = AnthropicChatClient(model="claude-opus-4-8", base_url="http://x")
    return client._build_payload(messages, stream=False)


class TestAnthropicSystemBlocks:
    def test_system_split_at_cache_boundary(self) -> None:
        parts = build_system_prompt_parts(
            _context(now=datetime(2026, 7, 7, 12, 0, tzinfo=UTC))
        )
        payload = _payload_for(
            [
                make_system_message(
                    parts.full, stable_prefix_chars=parts.stable_prefix_chars
                ),
                HumanMessage(content="hi"),
            ]
        )
        system = payload["system"]
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert system[0]["text"] == parts.stable
        assert "cache_control" not in system[1]
        assert "Current UTC time" in system[1]["text"]

    def test_prefix_block_is_byte_identical_across_turns(self) -> None:
        payloads = []
        for now, fragment in (
            (datetime(2026, 7, 7, 12, 0, 17, tzinfo=UTC), "- Likes green tea"),
            (datetime(2026, 7, 8, 9, 41, 3, tzinfo=UTC), "- Planning a Kyoto trip"),
        ):
            parts = build_system_prompt_parts(
                _context(
                    now=now,
                    fragments=(
                        MemoryBlock(label="retrieved_memories", value=fragment),
                    ),
                )
            )
            payloads.append(
                _payload_for(
                    [
                        make_system_message(
                            parts.full,
                            stable_prefix_chars=parts.stable_prefix_chars,
                        ),
                        HumanMessage(content="hi"),
                    ]
                )
            )
        first, second = (payload["system"][0] for payload in payloads)
        assert first == second

    def test_plain_string_without_boundary(self) -> None:
        payload = _payload_for(
            [SystemMessage(content="plain prompt"), HumanMessage(content="hi")]
        )
        assert payload["system"] == "plain prompt"

    def test_summary_messages_join_the_volatile_block(self) -> None:
        """Mid-conversation summaries are serialized as system parts; they
        must never land inside the cached prefix."""
        parts = build_system_prompt_parts(
            _context(now=datetime(2026, 7, 7, 12, 0, tzinfo=UTC))
        )
        payload = _payload_for(
            [
                make_system_message(
                    parts.full, stable_prefix_chars=parts.stable_prefix_chars
                ),
                SystemMessage(content="Conversation summary: earlier we discussed tea."),
                HumanMessage(content="hi"),
            ]
        )
        system = payload["system"]
        assert isinstance(system, list)
        assert "Conversation summary" not in system[0]["text"]
        assert "Conversation summary" in system[1]["text"]

    def test_tools_are_sorted_by_name(self) -> None:
        tools = [
            {"name": "zeta_tool", "input_schema": {"type": "object"}},
            {"name": "alpha_tool", "input_schema": {"type": "object"}},
            {"name": "mid_tool", "input_schema": {"type": "object"}},
        ]
        client = AnthropicChatClient(
            model="claude-opus-4-8", base_url="http://x", tools=tools
        )
        payload = client._build_payload([HumanMessage(content="hi")], stream=False)
        names = [tool["name"] for tool in payload["tools"]]
        assert names == sorted(names)
