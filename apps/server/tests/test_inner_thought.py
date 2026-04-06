"""Tests for thinking kwarg handling: executor strips thinking from args,
runtime carries inner thoughts through step traces for consolidation."""

from __future__ import annotations

from collections import deque

import pytest
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.executor import ToolExecutor, unpack_inner_thoughts_from_kwargs
from anima_server.services.agent.rules import (
    TerminalToolRule,
)
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StopReason,
    ToolCall,
)
from anima_server.services.agent.tools import (
    get_tool_rules,
    get_tools,
    send_message,
    tool,
)


class QueueAdapter(BaseLLMAdapter):
    provider = "test"
    model = "test-model"

    def __init__(self, responses: list[StepExecutionResult]) -> None:
        self._responses = deque(responses)
        self.requests: list[LLMRequest] = []

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("No queued responses.")
        return self._responses.popleft()


def test_inner_thought_not_in_default_tools() -> None:
    """inner_thought tool no longer exists in the default tool list."""
    tools = get_tools()
    names = [getattr(t, "name", "") for t in tools]
    assert "inner_thought" not in names


def test_no_init_tool_rule() -> None:
    """build_default_tool_rules has no InitToolRule (no inner_thought to gate)."""
    from anima_server.services.agent.rules import InitToolRule

    tools = get_tools()
    rules = get_tool_rules(tools)
    init_rules = [r for r in rules if isinstance(r, InitToolRule)]
    assert len(init_rules) == 0


# ---------------------------------------------------------------------------
# Unpack thinking from kwargs
# ---------------------------------------------------------------------------


def test_unpack_inner_thoughts_from_kwargs() -> None:
    """unpack_inner_thoughts_from_kwargs pops thinking from arguments."""
    tc = ToolCall(
        id="c1",
        name="send_message",
        arguments={"thinking": "reasoning here", "message": "hello"},
    )
    thought = unpack_inner_thoughts_from_kwargs(tc)
    assert thought == "reasoning here"
    assert "thinking" not in tc.arguments
    assert tc.arguments["message"] == "hello"


def test_unpack_inner_thoughts_missing() -> None:
    """Returns None when thinking kwarg is absent."""
    tc = ToolCall(id="c1", name="send_message", arguments={"message": "hello"})
    thought = unpack_inner_thoughts_from_kwargs(tc)
    assert thought is None


# ---------------------------------------------------------------------------
# Executor: thinking extracted before dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_strips_thinking_before_dispatch() -> None:
    """The executor should remove `thinking` from args before calling the tool."""

    @tool
    def echo_tool(msg: str) -> str:
        """Echo."""
        return msg

    executor = ToolExecutor([echo_tool])
    tc = ToolCall(
        id="c1",
        name="echo_tool",
        arguments={"thinking": "private reasoning", "msg": "public"},
    )
    result = await executor.execute(tc)

    import json as _json

    assert result.is_error is False
    assert _json.loads(result.output)["message"] == "public"
    assert result.inner_thinking == "private reasoning"


# ---------------------------------------------------------------------------
# Runtime: no InitToolRule needed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_direct_send_message_without_init_tool() -> None:
    """Model can call send_message directly — no init tool gate."""
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="send_message",
                        arguments={"thinking": "Simple greeting",
                                   "message": "Hey!"},
                    ),
                )
            ),
        ]
    )

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=2,
    )

    result = await runtime.invoke("hello", user_id=1, history=[])

    assert result.response == "Hey!"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert len(result.step_traces) == 1


# ---------------------------------------------------------------------------
# System prompt: cognitive loop instructions
# ---------------------------------------------------------------------------


def test_system_prompt_contains_cognitive_loop() -> None:
    """The system prompt includes the updated cognitive loop instructions."""
    from anima_server.services.agent.system_prompt import build_system_prompt

    prompt = build_system_prompt()
    assert "Cognitive Loop:" in prompt
    assert "thinking" in prompt
    assert "send_message" in prompt
    assert "inner_thought" not in prompt


def test_system_prompt_contains_memory_architecture() -> None:
    """The system prompt includes memory architecture guidance."""
    from anima_server.services.agent.system_prompt import build_system_prompt

    prompt = build_system_prompt()
    assert "Memory:" in prompt
    assert "core_memory_append" in prompt
    assert "save_to_memory" in prompt
    assert "self_working_memory" in prompt
    assert "note_to_self" in prompt


# ---------------------------------------------------------------------------
# History re-injection round-trip
# ---------------------------------------------------------------------------


def test_history_reinjection_round_trip() -> None:
    """StoredMessage with content (inner thought) + non-terminal tool_calls
    re-injects thinking into tool call args and clears content when replayed."""
    from anima_server.services.agent.messages import AIMessage, to_runtime_message
    from anima_server.services.agent.state import StoredMessage

    stored = StoredMessage(
        role="assistant",
        content="User seems happy today.",
        tool_calls=(
            ToolCall(
                id="c1",
                name="note_to_self",
                arguments={"key": "mood", "value": "happy"},
            ),
        ),
    )

    result = to_runtime_message(stored)

    assert isinstance(result, AIMessage)
    # Content should be cleared (thought moved into tool call args)
    assert result.content == ""
    # Thinking should be re-injected as first key in args
    assert result.tool_calls[0]["args"]["thinking"] == "User seems happy today."
    # Original args preserved
    assert result.tool_calls[0]["args"]["key"] == "mood"


def test_history_no_reinjection_for_send_message() -> None:
    """Assistant messages with send_message (terminal) keep content as-is.
    The content is real assistant text, not inner thinking."""
    from anima_server.services.agent.messages import AIMessage, to_runtime_message
    from anima_server.services.agent.state import StoredMessage

    stored = StoredMessage(
        role="assistant",
        content="Some assistant text",
        tool_calls=(
            ToolCall(
                id="c1",
                name="send_message",
                arguments={"message": "Hey!"},
            ),
        ),
    )

    result = to_runtime_message(stored)

    assert isinstance(result, AIMessage)
    # Content preserved — not re-injected because send_message is terminal
    assert result.content == "Some assistant text"
    assert "thinking" not in result.tool_calls[0]["args"]


def test_history_no_reinjection_without_tool_calls() -> None:
    """Assistant messages without tool_calls keep content as-is."""
    from anima_server.services.agent.messages import AIMessage, to_runtime_message
    from anima_server.services.agent.state import StoredMessage

    stored = StoredMessage(
        role="assistant",
        content="Just a plain text response.",
    )

    result = to_runtime_message(stored)

    assert isinstance(result, AIMessage)
    assert result.content == "Just a plain text response."
    assert result.tool_calls == []


# ---------------------------------------------------------------------------
# Inner thought extraction for consolidation
# ---------------------------------------------------------------------------


def test_extract_inner_thoughts_from_thinking_kwarg() -> None:
    """_extract_inner_thoughts extracts thinking from tool call arguments."""
    from anima_server.services.agent.runtime_types import StepTrace, ToolExecutionResult
    from anima_server.services.agent.service import _extract_inner_thoughts
    from anima_server.services.agent.state import AgentResult

    result = AgentResult(
        response="Hello!",
        model="test",
        provider="test",
        stop_reason="terminal_tool",
        tools_used=["send_message"],
        step_traces=[
            StepTrace(
                step_index=0,
                tool_calls=(
                    ToolCall(
                        id="c1",
                        name="send_message",
                        arguments={
                            "thinking": "User seems happy today.", "message": "Hey!"},
                    ),
                ),
                tool_results=(
                    ToolExecutionResult(
                        call_id="c1",
                        name="send_message",
                        output="Hey!",
                        inner_thinking="User seems happy today.",
                    ),
                ),
            ),
        ],
    )

    thoughts = _extract_inner_thoughts(result)
    assert "User seems happy today." in thoughts
