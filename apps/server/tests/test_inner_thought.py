"""Tests for guided inner monologue: think-first cognitive loop."""

from __future__ import annotations

import asyncio
from collections import deque

import pytest

from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.rules import (
    InitToolRule,
    TerminalToolRule,
    build_default_tool_rules,
)
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StopReason,
    ToolCall,
)
from anima_server.services.agent.tools import (
    continue_reasoning,
    get_tool_rules,
    get_tools,
    inner_thought,
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


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_inner_thought_in_default_tools() -> None:
    """inner_thought is registered in the default tool list."""
    tools = get_tools()
    names = [getattr(t, "name", "") for t in tools]
    assert "inner_thought" in names


def test_inner_thought_has_init_rule() -> None:
    """build_default_tool_rules includes an InitToolRule for inner_thought."""
    tools = get_tools()
    rules = get_tool_rules(tools)
    init_rules = [r for r in rules if isinstance(r, InitToolRule)]
    assert any(r.tool_name == "inner_thought" for r in init_rules)


def test_inner_thought_is_not_terminal() -> None:
    """inner_thought must not be terminal — it's always followed by action."""
    tools = get_tools()
    rules = get_tool_rules(tools)
    terminal_names = {
        r.tool_name for r in rules if isinstance(r, TerminalToolRule)
    }
    assert "inner_thought" not in terminal_names


def test_inner_thought_description_requires_thought_argument() -> None:
    assert "You MUST provide a thought string argument." in inner_thought.description


# ---------------------------------------------------------------------------
# Runtime: think-first enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inner_thought_must_be_first_tool() -> None:
    """If the agent tries to call send_message without thinking first,
    it gets a rule violation and must think first."""
    adapter = QueueAdapter([
        # Step 1: agent tries to call send_message directly (rule violation)
        StepExecutionResult(
            tool_calls=(ToolCall(id="c1", name="send_message", arguments={"message": "hi"}),)
        ),
        # Step 2: agent calls inner_thought (correct)
        StepExecutionResult(
            tool_calls=(ToolCall(id="c2", name="inner_thought", arguments={"thought": "greeting"}),)
        ),
        # Step 3: agent sends message (terminal)
        StepExecutionResult(
            tool_calls=(ToolCall(id="c3", name="send_message", arguments={"message": "Hello!"}),)
        ),
    ])

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[inner_thought, send_message],
        tool_rules=[
            InitToolRule(tool_name="inner_thought"),
            TerminalToolRule(tool_name="send_message"),
        ],
        max_steps=4,
    )

    result = await runtime.invoke("hi", user_id=1, history=[])

    assert result.response == "Hello!"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert len(result.step_traces) == 3
    # First step has rule violation
    assert result.step_traces[0].tool_results[0].is_error is True
    assert "first tool call must be one of: inner_thought" in result.step_traces[0].tool_results[0].output
    # Only inner_thought is available on first call
    assert adapter.requests[0].force_tool_call is True


@pytest.mark.asyncio
async def test_think_then_respond_happy_path() -> None:
    """Standard flow: think -> send_message."""
    adapter = QueueAdapter([
        StepExecutionResult(
            tool_calls=(ToolCall(id="c1", name="inner_thought", arguments={"thought": "User said hello."}),)
        ),
        StepExecutionResult(
            tool_calls=(ToolCall(id="c2", name="send_message", arguments={"message": "Hey there!"}),)
        ),
    ])

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[inner_thought, send_message],
        tool_rules=[
            InitToolRule(tool_name="inner_thought"),
            TerminalToolRule(tool_name="send_message"),
        ],
        max_steps=4,
    )

    result = await runtime.invoke("hello", user_id=1, history=[])

    assert result.response == "Hey there!"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert "inner_thought" in result.tools_used
    assert "send_message" in result.tools_used
    assert len(result.step_traces) == 2


@pytest.mark.asyncio
async def test_think_then_tool_then_respond() -> None:
    """Multi-step flow: think -> use a tool -> send_message."""

    @tool
    def lookup() -> str:
        """Look something up."""
        return "found it"

    adapter = QueueAdapter([
        StepExecutionResult(
            tool_calls=(ToolCall(id="c1", name="inner_thought", arguments={"thought": "Need to look something up."}),)
        ),
        StepExecutionResult(
            tool_calls=(ToolCall(id="c2", name="lookup", arguments={}),)
        ),
        StepExecutionResult(
            tool_calls=(ToolCall(id="c3", name="send_message", arguments={"message": "I found it!"}),)
        ),
    ])

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[inner_thought, lookup, send_message],
        tool_rules=[
            InitToolRule(tool_name="inner_thought"),
            TerminalToolRule(tool_name="send_message"),
        ],
        max_steps=4,
    )

    result = await runtime.invoke("search for X", user_id=1, history=[])

    assert result.response == "I found it!"
    assert len(result.step_traces) == 3
    assert result.tools_used == ["inner_thought", "lookup", "send_message"]


@pytest.mark.asyncio
async def test_think_then_continue_then_respond() -> None:
    """Flow with continue_reasoning: think -> continue -> send_message."""
    adapter = QueueAdapter([
        StepExecutionResult(
            tool_calls=(ToolCall(id="c1", name="inner_thought", arguments={"thought": "Complex question."}),)
        ),
        StepExecutionResult(
            tool_calls=(ToolCall(id="c2", name="continue_reasoning", arguments={}),)
        ),
        StepExecutionResult(
            tool_calls=(ToolCall(id="c3", name="send_message", arguments={"message": "Here's my analysis."}),)
        ),
    ])

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[inner_thought, continue_reasoning, send_message],
        tool_rules=[
            InitToolRule(tool_name="inner_thought"),
            TerminalToolRule(tool_name="send_message"),
        ],
        max_steps=5,
    )

    result = await runtime.invoke("complex", user_id=1, history=[])

    assert result.response == "Here's my analysis."
    assert "continue_reasoning" in result.tools_used
    assert len(result.step_traces) == 3


@pytest.mark.asyncio
async def test_inner_thought_content_not_in_response() -> None:
    """The inner thought content is captured in step traces but NOT
    sent as the response to the user."""
    adapter = QueueAdapter([
        StepExecutionResult(
            tool_calls=(ToolCall(
                id="c1", name="inner_thought",
                arguments={"thought": "SECRET INTERNAL REASONING"},
            ),)
        ),
        StepExecutionResult(
            tool_calls=(ToolCall(
                id="c2", name="send_message",
                arguments={"message": "Public response."},
            ),)
        ),
    ])

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[inner_thought, send_message],
        tool_rules=[
            InitToolRule(tool_name="inner_thought"),
            TerminalToolRule(tool_name="send_message"),
        ],
        max_steps=3,
    )

    result = await runtime.invoke("test", user_id=1, history=[])

    assert result.response == "Public response."
    assert "SECRET INTERNAL REASONING" not in result.response
    # But it IS in the step traces for observability
    inner_traces = [
        t for t in result.step_traces
        if t.tool_calls and t.tool_calls[0].name == "inner_thought"
    ]
    assert len(inner_traces) == 1
    assert inner_traces[0].tool_calls[0].arguments["thought"] == "SECRET INTERNAL REASONING"


# ---------------------------------------------------------------------------
# System prompt: cognitive loop instructions
# ---------------------------------------------------------------------------


def test_system_prompt_contains_cognitive_loop() -> None:
    """The system prompt includes the cognitive loop instructions."""
    from anima_server.services.agent.system_prompt import build_system_prompt
    prompt = build_system_prompt()
    assert "Cognitive Loop:" in prompt
    assert "THINK" in prompt
    assert "inner_thought" in prompt
    assert "send_message" in prompt


def test_system_prompt_contains_memory_architecture() -> None:
    """The system prompt includes memory architecture guidance."""
    from anima_server.services.agent.system_prompt import build_system_prompt
    prompt = build_system_prompt()
    assert "Memory Architecture:" in prompt
    assert "core_memory_append" in prompt
    assert "core_memory_replace" in prompt
    assert "save_to_memory" in prompt
