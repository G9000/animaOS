"""Tests for runtime enhancements: V3 loop auto-continuation, parallel tool
execution, tool timeout, memory refresh, streaming retry safety, and
proactive context management."""

from __future__ import annotations

import json as _json
from collections import deque
from unittest.mock import MagicMock, patch

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


def _msg(output: str) -> str:
    """Extract message from tool result JSON envelope."""
    return _json.loads(output)["message"]


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
    assert _msg(result.output) == "fast result"


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
    results = await executor.execute_parallel(
        [
            (ToolCall(id="c1", name="tool_a", arguments={}), False),
            (ToolCall(id="c2", name="tool_b", arguments={}), False),
        ]
    )

    assert len(results) == 2
    assert _msg(results[0].output) == "result_a"
    assert _msg(results[1].output) == "result_b"
    assert set(call_order) == {"a", "b"}


@pytest.mark.asyncio
async def test_parallel_execution_single_tool() -> None:
    """Single-tool parallel execution works normally."""

    @tool
    def only_tool() -> str:
        """Single tool."""
        return "only"

    executor = ToolExecutor([only_tool])
    results = await executor.execute_parallel(
        [
            (ToolCall(id="c1", name="only_tool", arguments={}), False),
        ]
    )

    assert len(results) == 1
    assert _msg(results[0].output) == "only"


# ---------------------------------------------------------------------------
# Memory Modified Flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_modified_flag_propagated() -> None:
    """When tool sets ctx.memory_modified, it appears on the result."""
    from unittest.mock import MagicMock

    from anima_server.services.agent.tool_context import (
        ToolContext,
        clear_tool_context,
        set_tool_context,
    )

    @tool
    def modify_memory() -> str:
        """Modify memory."""
        from anima_server.services.agent.tool_context import get_tool_context

        ctx = get_tool_context()
        ctx.memory_modified = True
        return "modified"

    mock_db = MagicMock()
    mock_runtime_db = MagicMock()
    set_tool_context(ToolContext(db=mock_db, runtime_db=mock_runtime_db, user_id=1, thread_id=1))
    try:
        executor = ToolExecutor([modify_memory])
        tc = ToolCall(id="c1", name="modify_memory", arguments={})
        result = await executor.execute(tc)

        assert result.memory_modified is True
        assert _msg(result.output) == "modified"
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


@pytest.mark.asyncio
async def test_note_to_self_signals_memory_modified(monkeypatch) -> None:
    from anima_server.services.agent import companion, session_memory
    from anima_server.services.agent.tool_context import (
        ToolContext,
        clear_tool_context,
        set_tool_context,
    )
    from anima_server.services.agent.tools import note_to_self

    monkeypatch.setattr(session_memory, "write_session_note", lambda *args, **kwargs: None)
    monkeypatch.setattr(companion, "get_companion", lambda user_id: None)

    set_tool_context(
        ToolContext(
            db=MagicMock(),
            runtime_db=MagicMock(),
            user_id=1,
            thread_id=1,
        )
    )
    try:
        executor = ToolExecutor([note_to_self])
        tc = ToolCall(
            id="c1",
            name="note_to_self",
            arguments={"key": "mood", "value": "calm"},
        )
        result = await executor.execute(tc)

        assert result.memory_modified is True
        assert _msg(result.output) == "Noted: mood"
    finally:
        clear_tool_context()


@pytest.mark.asyncio
async def test_dismiss_note_signals_memory_modified(monkeypatch) -> None:
    from anima_server.services.agent import companion, session_memory
    from anima_server.services.agent.tool_context import (
        ToolContext,
        clear_tool_context,
        set_tool_context,
    )
    from anima_server.services.agent.tools import dismiss_note

    monkeypatch.setattr(session_memory, "remove_session_note", lambda *args, **kwargs: True)
    monkeypatch.setattr(companion, "get_companion", lambda user_id: None)

    set_tool_context(
        ToolContext(
            db=MagicMock(),
            runtime_db=MagicMock(),
            user_id=1,
            thread_id=1,
        )
    )
    try:
        executor = ToolExecutor([dismiss_note])
        tc = ToolCall(
            id="c1",
            name="dismiss_note",
            arguments={"key": "mood"},
        )
        result = await executor.execute(tc)

        assert result.memory_modified is True
        assert _msg(result.output) == "Dismissed note: mood"
    finally:
        clear_tool_context()


@pytest.mark.asyncio
async def test_save_to_memory_signals_memory_modified(monkeypatch) -> None:
    from anima_server.services.agent import companion, session_memory
    from anima_server.services.agent.tool_context import (
        ToolContext,
        clear_tool_context,
        set_tool_context,
    )
    from anima_server.services.agent.tools import save_to_memory

    monkeypatch.setattr(session_memory, "promote_session_note", lambda *args, **kwargs: True)
    monkeypatch.setattr(companion, "get_companion", lambda user_id: None)

    set_tool_context(
        ToolContext(
            db=MagicMock(),
            runtime_db=MagicMock(),
            user_id=1,
            thread_id=1,
        )
    )
    try:
        executor = ToolExecutor([save_to_memory])
        tc = ToolCall(
            id="c1",
            name="save_to_memory",
            arguments={"key": "likes coffee"},
        )
        result = await executor.execute(tc)

        assert result.memory_modified is True
        assert _msg(result.output) == "Saved 'likes coffee' to permanent memory (category: fact)"
    finally:
        clear_tool_context()


@pytest.mark.asyncio
async def test_set_intention_signals_memory_modified(monkeypatch) -> None:
    from anima_server.services.agent import companion, intentions
    from anima_server.services.agent.tool_context import (
        ToolContext,
        clear_tool_context,
        set_tool_context,
    )
    from anima_server.services.agent.tools import set_intention

    monkeypatch.setattr(intentions, "add_intention", lambda *args, **kwargs: None)
    monkeypatch.setattr(companion, "get_companion", lambda user_id: None)

    set_tool_context(
        ToolContext(
            db=MagicMock(),
            runtime_db=MagicMock(),
            user_id=1,
            thread_id=1,
        )
    )
    try:
        executor = ToolExecutor([set_intention])
        tc = ToolCall(
            id="c1",
            name="set_intention",
            arguments={"title": "Ship the runtime audit fixes"},
        )
        result = await executor.execute(tc)

        assert result.memory_modified is True
        assert _msg(result.output) == "Tracking intention: Ship the runtime audit fixes"
    finally:
        clear_tool_context()


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
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(id="c1", name="modify_tool", arguments={}),
                )
            ),
            StepExecutionResult(
                tool_calls=(ToolCall(id="c2", name="send_message", arguments={"message": "done"}),)
            ),
        ]
    )

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
        "hi",
        user_id=1,
        history=[],
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
async def test_v3_auto_continue_after_non_terminal_tool() -> None:
    """Non-terminal tools auto-continue the loop (V3-style, no heartbeat needed)."""

    @tool
    def lookup(query: str) -> str:
        """Search for something."""
        return "found it"

    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(id="c1", name="lookup", arguments={"query": "test"}),
                )
            ),
            StepExecutionResult(
                tool_calls=(
                    ToolCall(
                        id="c2", name="send_message", arguments={"message": "thought it through"}
                    ),
                )
            ),
        ]
    )

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[lookup, send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=3,
    )

    result = await runtime.invoke("complex question", user_id=1, history=[])

    assert result.response == "thought it through"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert "lookup" in result.tools_used
    assert len(result.step_traces) == 2


@pytest.mark.asyncio
async def test_v3_max_steps_without_terminal_tool() -> None:
    """When max_steps is exhausted without send_message, stop reason is NO_TERMINAL_TOOL."""

    @tool
    def lookup(query: str) -> str:
        """Search for something."""
        return "found it"

    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(id="c1", name="lookup", arguments={"query": "test"}),
                )
            ),
        ]
    )

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[lookup, send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=1,
    )

    result = await runtime.invoke("search", user_id=1, history=[])

    assert result.stop_reason == StopReason.NO_TERMINAL_TOOL.value
    assert len(result.step_traces) == 1


# ---------------------------------------------------------------------------
# Streaming Retry Safety
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_retry_blocked_after_content_streamed() -> None:
    """If content was already streamed to the client, retries must not
    happen (would cause duplicate output)."""
    adapter = StreamFailAfterContentAdapter(
        deltas=["Hello ", "world"],
        fail_exc=TimeoutError(),
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
                "hi",
                user_id=1,
                history=[],
                event_callback=collect,
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
    from anima_server.services.agent.tools import get_core_tools, get_tools

    tool_names = [getattr(t, "name", "") for t in get_tools()]
    assert "core_memory_append" in tool_names
    assert "core_memory_replace" in tool_names
    assert "inner_thought" not in tool_names  # removed in favor of thinking kwarg
    # Core tools are a subset of all tools
    core_names = [getattr(t, "name", "") for t in get_core_tools()]
    assert set(core_names) <= set(tool_names)


def test_heartbeat_not_injected_on_any_tool() -> None:
    """V3 loop: request_heartbeat should NOT be injected on any tool."""
    from anima_server.services.agent.tools import get_tools

    tools = get_tools()
    for t in tools:
        name = getattr(t, "name", "")
        schema = t.args_schema.model_json_schema()
        props = schema.get("properties", {})
        assert "request_heartbeat" not in props, (
            f"{name} should not have request_heartbeat (V3 loop)"
        )


# ---------------------------------------------------------------------------
# Bug C1: rules_solver.update_state() on coerced tool-call path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coerced_text_tool_call_as_send_message() -> None:
    """When the model emits plain text (no native tool call), it should
    be coerced into a send_message call."""
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                assistant_text="Here is my answer.",
            ),
        ]
    )

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[send_message],
        tool_rules=[
            TerminalToolRule(tool_name="send_message"),
        ],
        max_steps=4,
    )

    result = await runtime.invoke("What do you think?", user_id=1, history=[])

    assert result.response == "Here is my answer."
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert "send_message" in result.tools_used


@pytest.mark.asyncio
async def test_coerced_function_tag_tool_call() -> None:
    """When a model emits a <function=tool_name> tag, it should be
    parsed and executed as a tool call."""

    @tool
    def lookup(query: str) -> str:
        """Look something up."""
        return f"found: {query}"

    adapter = QueueAdapter(
        [
            StepExecutionResult(
                assistant_text='<function=lookup>{"query": "test"}</function>',
            ),
            StepExecutionResult(
                tool_calls=(ToolCall(id="c2", name="send_message", arguments={"message": "done"}),),
            ),
        ]
    )

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[lookup, send_message],
        tool_rules=[
            TerminalToolRule(tool_name="send_message"),
        ],
        max_steps=4,
    )

    result = await runtime.invoke("test", user_id=1, history=[])

    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert "lookup" in result.tools_used


# ---------------------------------------------------------------------------
# Bug C2: <parameter=name> tags inside <function=...> blocks
# ---------------------------------------------------------------------------


def test_parse_function_tag_with_parameter_tags() -> None:
    """<parameter=name>value</parameter> tags inside <function=...> blocks
    should be parsed into a proper argument dict."""
    from anima_server.services.agent.runtime import _parse_function_tag_tool_calls

    text = (
        "<function=save_to_memory>\n"
        "<parameter=category>\n"
        "goal\n"
        "</parameter>\n"
        "<parameter=importance>\n"
        "5\n"
        "</parameter>\n"
        "<parameter=text>\n"
        "Finish memory module by end of week\n"
        "</parameter>\n"
        "</function>"
    )

    results = _parse_function_tag_tool_calls(text, {"save_to_memory"})

    assert len(results) == 1
    assert results[0].name == "save_to_memory"
    assert results[0].arguments["category"] == "goal"
    assert results[0].arguments["importance"] == "5"
    assert results[0].arguments["text"] == "Finish memory module by end of week"


def test_parse_function_tag_with_parameter_tags_no_closing_function() -> None:
    """Parameter tags should parse even when </function> is absent."""
    from anima_server.services.agent.runtime import _parse_function_tag_tool_calls

    text = (
        "<function=save_to_memory>\n"
        "<parameter=key>goal_1</parameter>\n"
        "<parameter=text>Save this goal</parameter>"
    )

    results = _parse_function_tag_tool_calls(text, {"save_to_memory"})

    assert len(results) == 1
    assert results[0].arguments["key"] == "goal_1"
    assert results[0].arguments["text"] == "Save this goal"


def test_parse_function_tag_json_still_preferred_over_parameter_tags() -> None:
    """When content is valid JSON, it should be used even if parameter tags
    could theoretically be parsed."""
    from anima_server.services.agent.runtime import _parse_function_tag_tool_calls

    text = '<function=note_to_self>{"key": "mood", "value": "happy"}</function>'

    results = _parse_function_tag_tool_calls(text, {"note_to_self"})

    assert len(results) == 1
    assert results[0].arguments == {"key": "mood", "value": "happy"}


def test_parse_function_tag_plain_text_fallback_still_works() -> None:
    """Plain text content (no JSON, no parameter tags) should still fall
    back to _infer_first_arg_name."""
    from anima_server.services.agent.runtime import _parse_function_tag_tool_calls

    text = "<function=send_message>Hello there!</function>"

    results = _parse_function_tag_tool_calls(text, {"send_message"})

    assert len(results) == 1
    assert results[0].name == "send_message"
    assert "Hello there!" in next(iter(results[0].arguments.values()))


# ---------------------------------------------------------------------------
# V3 Loop: Parallel Tool Call Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_mixed_terminal_and_non_terminal() -> None:
    """Non-terminal tools execute in parallel, send_message executes last."""

    @tool
    def recall_memory(query: str) -> str:
        """Search memory."""
        return "found: user likes cats"

    @tool
    def current_datetime() -> str:
        """Get current time."""
        return "2026-03-29T12:00:00Z"

    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(id="c1", name="recall_memory", arguments={"query": "pets"}),
                    ToolCall(id="c2", name="current_datetime", arguments={}),
                    ToolCall(id="c3", name="send_message", arguments={"message": "You like cats!"}),
                )
            ),
        ]
    )

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[recall_memory, current_datetime, send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=3,
    )

    result = await runtime.invoke("what do I like?", user_id=1, history=[])

    assert result.response == "You like cats!"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert "recall_memory" in result.tools_used
    assert "current_datetime" in result.tools_used
    assert len(result.step_traces) == 1
    assert len(result.step_traces[0].tool_results) == 3


@pytest.mark.asyncio
async def test_parallel_partial_failure_continues() -> None:
    """When one tool fails in a parallel batch, the loop still continues."""

    @tool
    def good_tool(query: str) -> str:
        """A tool that works."""
        return "success"

    @tool
    def bad_tool(query: str) -> str:
        """A tool that always fails."""
        raise ValueError("something broke")

    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(id="c1", name="good_tool", arguments={"query": "test"}),
                    ToolCall(id="c2", name="bad_tool", arguments={"query": "test"}),
                )
            ),
            StepExecutionResult(
                tool_calls=(
                    ToolCall(id="c3", name="send_message", arguments={"message": "recovered"}),
                )
            ),
        ]
    )

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[good_tool, bad_tool, send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=3,
    )

    result = await runtime.invoke("test both", user_id=1, history=[])

    assert result.response == "recovered"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert len(result.step_traces) == 2
    step1_results = result.step_traces[0].tool_results
    assert len(step1_results) == 2
    assert any(tr.is_error for tr in step1_results)
    assert any(not tr.is_error for tr in step1_results)


@pytest.mark.asyncio
async def test_v3_three_consecutive_non_terminal_steps() -> None:
    """V3 loop auto-continues through 3+ consecutive non-terminal steps."""

    @tool
    def step_a() -> str:
        """First step."""
        return "a done"

    @tool
    def step_b() -> str:
        """Second step."""
        return "b done"

    @tool
    def step_c() -> str:
        """Third step."""
        return "c done"

    adapter = QueueAdapter(
        [
            StepExecutionResult(tool_calls=(ToolCall(id="c1", name="step_a", arguments={}),)),
            StepExecutionResult(tool_calls=(ToolCall(id="c2", name="step_b", arguments={}),)),
            StepExecutionResult(tool_calls=(ToolCall(id="c3", name="step_c", arguments={}),)),
            StepExecutionResult(
                tool_calls=(
                    ToolCall(id="c4", name="send_message", arguments={"message": "all steps done"}),
                )
            ),
        ]
    )

    runtime = AgentRuntime(
        adapter=adapter,
        tools=[step_a, step_b, step_c, send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=5,
    )

    result = await runtime.invoke("do all steps", user_id=1, history=[])

    assert result.response == "all steps done"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert len(result.step_traces) == 4
    assert "step_a" in result.tools_used
    assert "step_b" in result.tools_used
    assert "step_c" in result.tools_used
