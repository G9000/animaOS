"""ARH-013 #1 — characterization tests for the step tool-call pipeline.

These lock the current `invoke` behavior of the three sub-behaviors that had
no dedicated coverage before extracting the shared `_process_step_tool_calls`
helper: deferred blocked-tool execution, consecutive-failure exclusion, and
the flush-on-approval / drop-on-violation ordering quirk.  They must stay
green byte-for-byte after the extraction (pure refactor of `invoke`), and are
extended to `resume_after_approval` once it is wired to the shared helper.
"""

from __future__ import annotations

from collections import deque

import pytest
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.rules import (
    InitToolRule,
    RequiresApprovalToolRule,
    TerminalToolRule,
)
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StopReason,
    ToolCall,
)
from anima_server.services.agent.tools import current_datetime, send_message, tool


class QueueAdapter(BaseLLMAdapter):
    provider = "test"
    model = "test-model"

    def __init__(self, responses: list[StepExecutionResult]) -> None:
        self._responses = deque(responses)
        self.requests: list[LLMRequest] = []

    async def invoke(self, request: LLMRequest) -> StepExecutionResult:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("No queued LLM responses remain for the test adapter.")
        return self._responses.popleft()


# --------------------------------------------------------------------------- #
# Deferred blocked-tool execution
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_deferred_blocked_tool_executes_after_terminal_turn() -> None:
    """A non-terminal, non-init tool blocked by a rule is deferred, then
    executed post-turn once the turn ends on a terminal tool."""

    @tool
    def think() -> str:
        """Record an internal planning step."""
        return "planned"

    adapter = QueueAdapter(
        [
            # Step 0: call current_datetime first — violates InitToolRule(think),
            # current_datetime is deferrable so it is queued, not discarded.
            StepExecutionResult(
                tool_calls=(ToolCall(id="c0", name="current_datetime", arguments={}),)
            ),
            # Step 1: satisfy the init tool, then terminate.
            StepExecutionResult(
                tool_calls=(ToolCall(id="c1", name="think", arguments={}),)
            ),
            StepExecutionResult(
                tool_calls=(
                    ToolCall(id="c2", name="send_message", arguments={"message": "done"}),
                )
            ),
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[think, current_datetime, send_message],
        tool_rules=[InitToolRule(tool_name="think"), TerminalToolRule(tool_name="send_message")],
        max_steps=5,
    )

    result = await runtime.invoke("start", user_id=1, history=[])

    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert result.response == "done"
    # The deferred current_datetime ran post-turn (turn ended terminal).
    assert "current_datetime" in result.tools_used
    assert "think" in result.tools_used


@pytest.mark.asyncio
async def test_deferred_blocked_tool_dropped_when_turn_not_terminal() -> None:
    """If the turn does not end on a terminal tool, deferred calls are not
    executed (only logged)."""

    @tool
    def think() -> str:
        """Record an internal planning step."""
        return "planned"

    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(ToolCall(id="c0", name="current_datetime", arguments={}),)
            ),
            StepExecutionResult(assistant_text="Recovered without running the deferred tool."),
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[think, current_datetime],
        tool_rules=[InitToolRule(tool_name="think")],
        max_steps=3,
    )

    result = await runtime.invoke("start", user_id=1, history=[])

    assert result.stop_reason == StopReason.END_TURN.value
    assert result.tools_used == []  # current_datetime was deferred, never run


# --------------------------------------------------------------------------- #
# Consecutive-failure tool exclusion
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tool_excluded_after_two_consecutive_failures() -> None:
    """A tool that errors on two consecutive steps is dropped from the allowed
    set on the third step (one retry is granted before exclusion)."""

    @tool
    def flaky() -> str:
        """Always fails."""
        raise RuntimeError("boom")

    adapter = QueueAdapter(
        [
            StepExecutionResult(tool_calls=(ToolCall(id="c0", name="flaky", arguments={}),)),
            StepExecutionResult(tool_calls=(ToolCall(id="c1", name="flaky", arguments={}),)),
            StepExecutionResult(
                tool_calls=(
                    ToolCall(id="c2", name="send_message", arguments={"message": "gave up"}),
                )
            ),
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[flaky, send_message],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=5,
    )

    result = await runtime.invoke("go", user_id=1, history=[])

    assert result.response == "gave up"
    # Step 0 and 1 both offered flaky (one retry granted); step 2 excluded it.
    step0_tools = [t.name for t in adapter.requests[0].available_tools]
    step1_tools = [t.name for t in adapter.requests[1].available_tools]
    step2_tools = [t.name for t in adapter.requests[2].available_tools]
    assert "flaky" in step0_tools
    assert "flaky" in step1_tools
    assert "flaky" not in step2_tools
    assert "send_message" in step2_tools


# --------------------------------------------------------------------------- #
# Flush-on-approval vs drop-on-violation ordering quirk
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_validated_calls_flushed_before_approval_stop() -> None:
    """When a batch is [safe_non_terminal, approval_tool], the safe tool
    already collected is executed before stopping for approval."""

    ran: list[str] = []

    @tool
    def safe() -> str:
        """A safe non-terminal tool."""
        ran.append("safe")
        return "ok"

    @tool
    def danger() -> str:
        """A tool requiring approval."""
        ran.append("danger")
        return "boom"

    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(id="c0", name="safe", arguments={}),
                    ToolCall(id="c1", name="danger", arguments={}),
                )
            ),
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[safe, danger, send_message],
        tool_rules=[
            RequiresApprovalToolRule(tool_name="danger"),
            TerminalToolRule(tool_name="send_message"),
        ],
        max_steps=3,
    )

    result = await runtime.invoke("go", user_id=1, history=[])

    assert result.stop_reason == StopReason.AWAITING_APPROVAL.value
    assert "safe" in ran  # flushed before the approval stop
    assert "danger" not in ran  # never executed — awaiting approval
    assert "safe" in result.tools_used


# --------------------------------------------------------------------------- #
# resume_after_approval shares the same pipeline
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resume_follow_up_runs_shared_multi_tool_pipeline() -> None:
    """After extraction, the approval-resume follow-up processes a multi-tool
    batch through the same `_process_step_tool_calls` helper as `invoke`:
    non-terminal executed in parallel, terminal last, response captured."""

    @tool
    def search(query: str) -> str:
        """Search (requires approval)."""
        return f"results: {query}"

    @tool
    def note(text: str) -> str:
        """Record a non-terminal note."""
        return f"noted: {text}"

    adapter = QueueAdapter(
        [
            # The single follow-up LLM call returns a non-terminal + terminal batch.
            StepExecutionResult(
                tool_calls=(
                    ToolCall(id="f0", name="note", arguments={"text": "hi"}),
                    ToolCall(
                        id="f1", name="send_message", arguments={"message": "all done"}
                    ),
                )
            ),
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[search, note, send_message],
        tool_rules=[
            RequiresApprovalToolRule(tool_name="search"),
            TerminalToolRule(tool_name="send_message"),
        ],
        max_steps=4,
    )

    result = await runtime.resume_after_approval(
        approved=True,
        tool_call=ToolCall(id="c0", name="search", arguments={"query": "prefs"}),
        user_id=1,
        history=[],
    )

    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert result.response == "all done"
    # The approved tool (step 0) plus both follow-up tools all ran.
    assert result.tools_used == ["search", "note", "send_message"]
