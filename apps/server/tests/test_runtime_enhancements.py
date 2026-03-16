"""Tests for runtime enhancements: tool timeout, memory refresh, continue_reasoning,
streaming retry safety, and proactive context management."""

from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import patch

import pytest

from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.executor import ToolExecutor
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.rules import TerminalToolRule
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StepFailedError,
    StepStreamEvent,
    StopReason,
    ToolCall,
    ToolExecutionResult,
)
from anima_server.services.agent.streaming import AgentStreamEvent
from anima_server.services.agent.tools import send_message, tool


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


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


class StreamFailAfterContentAdapter(BaseLLMAdapter):
    """Streams some content then raises an error."""

    provider = "test"
    model = "test-model"

    def __init__(self, *, deltas: list[str], fail_exc: Exception) -> None:
        self._deltas = deltas
        self._fail_exc = fail_exc
        self.call_count = 0

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        raise self._fail_exc

    async def stream(self, request: LLMRequest):
        self.call_count += 1
        for delta in self._deltas:
            yield StepStreamEvent(content_delta=delta)
        raise self._fail_exc


# ---------------------------------------------------------------------------
# Tool Timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_timeout_returns_error() -> None:
    """A tool that exceeds agent_tool_timeout produces a timeout error."""

    @tool
    def slow_tool() -> str:
        """Do something slow."""
        import time
        time.sleep(0.5)
        return "done"

    executor = ToolExecutor([slow_tool])
    tc = ToolCall(id="c1", name="slow_tool", arguments={})

    with patch("anima_server.services.agent.executor.settings") as mock:
        mock.agent_tool_timeout = 0.05  # 50ms timeout
        result = await executor.execute(tc)

    assert result.is_error is True
    assert "timed out" in result.output


@pytest.mark.asyncio
async def test_tool_timeout_does_not_affect_fast_tools() -> None:
    """Fast tools complete normally within the timeout."""

    @tool
    def fast_tool() -> str:
        """Quick operation."""
        return "fast result"

    executor = ToolExecutor([fast_tool])
    tc = ToolCall(id="c1", name="fast_tool", arguments={})

    with patch("anima_server.services.agent.executor.settings") as mock:
        mock.agent_tool_timeout = 5.0
        result = await executor.execute(tc)

    assert result.is_error is False
    assert result.output == "fast result"


# ---------------------------------------------------------------------------
# Parallel Tool Execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_execution_runs_concurrently() -> None:
    """execute_parallel runs multiple tools and returns results in order."""
    call_order: list[str] = []

    @tool
    def tool_a() -> str:
        """Tool A."""
        call_order.append("a")
        return "result_a"

    @tool
    def tool_b() -> str:
        """Tool B."""
        call_order.append("b")
        return "result_b"

    executor = ToolExecutor([tool_a, tool_b])
    results = await executor.execute_parallel([
        (ToolCall(id="c1", name="tool_a", arguments={}), False),
        (ToolCall(id="c2", name="tool_b", arguments={}), False),
    ])

    assert len(results) == 2
    assert results[0].output == "result_a"
    assert results[1].output == "result_b"
    assert set(call_order) == {"a", "b"}


@pytest.mark.asyncio
async def test_parallel_execution_single_tool() -> None:
    """Single-tool parallel execution works normally."""

    @tool
    def only_tool() -> str:
        """Single tool."""
        return "only"

    executor = ToolExecutor([only_tool])
    results = await executor.execute_parallel([
        (ToolCall(id="c1", name="only_tool", arguments={}), False),
    ])

    assert len(results) == 1
    assert results[0].output == "only"


# ---------------------------------------------------------------------------
# Memory Modified Flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_modified_flag_propagated() -> None:
    """When tool sets ctx.memory_modified, it appears on the result."""
    from anima_server.services.agent.tool_context import ToolContext, set_tool_context, clear_tool_context
    from unittest.mock import MagicMock

    @tool
    def modify_memory() -> str:
        """Modify memory."""
        from anima_server.services.agent.tool_context import get_tool_context
        ctx = get_tool_context()
        ctx.memory_modified = True
        return "modified"

    mock_db = MagicMock()
    set_tool_context(ToolContext(db=mock_db, user_id=1, thread_id=1))
    try:
        executor = ToolExecutor([modify_memory])
        tc = ToolCall(id="c1", name="modify_memory", arguments={})
        result = await executor.execute(tc)

        assert result.memory_modified is True
        assert result.output == "modified"
    finally:
        clear_tool_context()


@pytest.mark.asyncio
async def test_memory_modified_flag_false_by_default() -> None:
    """Tools that don't set memory_modified have it False."""

    @tool
    def normal_tool() -> str:
        """Normal tool."""
        return "normal"

    executor = ToolExecutor([normal_tool])
    tc = ToolCall(id="c1", name="normal_tool", arguments={})
    result = await executor.execute(tc)

    assert result.memory_modified is False


# ---------------------------------------------------------------------------
# Memory Refresh Between Steps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_refresh_callback_updates_system_prompt() -> None:
    """When a tool signals memory_modified, the runtime calls the refresher
    and rebuilds the system prompt for the next step."""
    refresh_called = False

    @tool
    def modify_tool() -> str:
        """Modify memory."""
        return "modified"

    # Step 1: tool call that signals memory_modified
    # Step 2: send_message with final response
    adapter = QueueAdapter([
        StepExecutionResult(
            tool_calls=(ToolCall(id="c1", name="modify_tool", arguments={}),)
        ),
        StepExecutionResult(
            tool_calls=(ToolCall(id="c2", name="send_message", arguments={"message": "done"}),)
        ),
    ])

    # Custom executor that sets memory_modified
    class MemModifiedExecutor(ToolExecutor):
        async def execute(self, tool_call, *, is_terminal=False):
            result = await super().execute(tool_call, is_terminal=is_terminal)
            if tool_call.name == "modify_tool":
                return ToolExecutionResult(
                    call_id=result.call_id,
                    name=result.name,
                    output=result.output,
                    is_terminal=result.is_terminal,
                    memory_modified=True,
                )
            return result

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[modify_tool, send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        tool_executor=MemModifiedExecutor([modify_tool, send_message]),
        max_steps=3,
    )

    updated_block = MemoryBlock(
        label="human",
        value="Updated: user likes coffee AND tea",
        description="User info",
    )

    async def refresher():
        nonlocal refresh_called
        refresh_called = True
        return (updated_block,)

    result = await runtime.invoke(
        "hi", user_id=1, history=[],
        memory_refresher=refresher,
    )

    assert result.response == "done"
    assert refresh_called is True
    # The second request should have the updated system prompt
    assert len(adapter.requests) == 2
    second_system = adapter.requests[1].messages[0].content
    assert "Updated: user likes coffee AND tea" in second_system


# ---------------------------------------------------------------------------
# Continue Reasoning Tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_continue_reasoning_allows_multi_step() -> None:
    """The continue_reasoning tool is non-terminal, allowing the agent
    to chain steps before sending a final response."""
    from anima_server.services.agent.tools import continue_reasoning

    adapter = QueueAdapter([
        # Step 1: agent calls continue_reasoning
        StepExecutionResult(
            tool_calls=(ToolCall(id="c1", name="continue_reasoning", arguments={}),)
        ),
        # Step 2: agent sends final message
        StepExecutionResult(
            tool_calls=(ToolCall(id="c2", name="send_message", arguments={"message": "thought it through"}),)
        ),
    ])

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[continue_reasoning, send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=3,
    )

    result = await runtime.invoke("complex question", user_id=1, history=[])

    assert result.response == "thought it through"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert "continue_reasoning" in result.tools_used
    assert len(result.step_traces) == 2


# ---------------------------------------------------------------------------
# Streaming Retry Safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_retry_blocked_after_content_streamed() -> None:
    """If content was already streamed to the client, retries must not
    happen (would cause duplicate output)."""
    adapter = StreamFailAfterContentAdapter(
        deltas=["Hello ", "world"],
        fail_exc=asyncio.TimeoutError(),
    )
    runtime = AgentRuntime(adapter=adapter, tools=[], max_steps=1)

    events: list[AgentStreamEvent] = []

    async def collect(event: AgentStreamEvent) -> None:
        events.append(event)

    with patch("anima_server.services.agent.runtime.settings") as mock_settings:
        mock_settings.agent_llm_timeout = 5.0
        mock_settings.agent_llm_retry_limit = 3
        mock_settings.agent_llm_retry_backoff_factor = 0.01
        mock_settings.agent_llm_retry_max_delay = 0.05
        mock_settings.agent_max_steps = 1

        with pytest.raises(StepFailedError):
            await runtime.invoke(
                "hi", user_id=1, history=[], event_callback=collect,
            )

    # Should NOT retry — content was already streamed
    assert adapter.call_count == 1
    # Client received partial content
    chunk_events = [e for e in events if e.event == "chunk"]
    assert len(chunk_events) == 2


# ---------------------------------------------------------------------------
# Core Memory Tools (unit tests)
# ---------------------------------------------------------------------------


def test_core_memory_tools_registered() -> None:
    """core_memory_append and core_memory_replace are in get_tools()."""
    from anima_server.services.agent.tools import get_tools
    tool_names = [getattr(t, "name", "") for t in get_tools()]
    assert "core_memory_append" in tool_names
    assert "core_memory_replace" in tool_names
    assert "continue_reasoning" in tool_names


def test_continue_reasoning_tool_is_not_terminal() -> None:
    """continue_reasoning must not be terminal — it allows chaining."""
    from anima_server.services.agent.tools import get_tools, get_tool_rules
    tools = get_tools()
    rules = get_tool_rules(tools)
    terminal_names = {
        r.tool_name for r in rules if isinstance(r, TerminalToolRule)
    }
    assert "continue_reasoning" not in terminal_names
    assert "send_message" in terminal_names
