from __future__ import annotations

import asyncio
import json
from collections import deque

import pytest
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.delegation import DelegatedToolResult
from anima_server.services.agent.executor import ToolExecutor
from anima_server.services.agent.rules import TerminalToolRule
from anima_server.services.agent.runtime import AgentRuntime
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StopReason,
    ToolCall,
    ToolExecutionResult,
)


def _extract_message(output: str) -> str:
    return json.loads(output)["message"]


class LocalTool:
    name = "delegated_action"

    async def ainvoke(self, payload: dict[str, object]) -> str:
        return f"local:{payload['value']}"


class ReplyTool:
    name = "reply"

    async def ainvoke(self, payload: dict[str, object]) -> str:
        return f"local:{payload['message']}"


class SendMessageTool:
    name = "send_message"

    async def ainvoke(self, payload: dict[str, object]) -> str:
        return str(payload["message"])


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


class RecordingExecutor(ToolExecutor):
    def __init__(self, tools: list[object], output: str) -> None:
        super().__init__(tools)
        self._output = output
        self.calls: list[tuple[str, bool]] = []

    async def execute(
        self,
        tool_call: ToolCall,
        *,
        is_terminal: bool = False,
    ) -> ToolExecutionResult:
        self.calls.append((tool_call.name, is_terminal))
        return ToolExecutionResult(
            call_id=tool_call.id,
            name=tool_call.name,
            output=self._output,
            is_terminal=is_terminal,
        )


@pytest.mark.asyncio
async def test_executor_without_delegation_runs_locally() -> None:
    executor = ToolExecutor([LocalTool()])

    result = await executor.execute(
        ToolCall(id="call-1", name="delegated_action", arguments={"value": 1})
    )

    assert result.is_error is False
    assert _extract_message(result.output) == "local:1"


@pytest.mark.asyncio
async def test_executor_with_delegation_forwards_matching_tools() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    async def delegate(
        call_id: str, tool_name: str, args: dict[str, object]
    ) -> DelegatedToolResult:
        calls.append((call_id, tool_name, args))
        return DelegatedToolResult(
            call_id=call_id,
            name=tool_name,
            output=f"delegated:{args['value']}",
        )

    executor = ToolExecutor(
        [LocalTool()],
        delegate=delegate,
        delegated_tool_names=frozenset({"delegated_action"}),
    )

    result = await executor.execute(
        ToolCall(id="call-2", name="delegated_action", arguments={"value": 7})
    )

    assert result.is_error is False
    assert _extract_message(result.output) == "delegated:7"
    assert calls == [("call-2", "delegated_action", {"value": 7})]


@pytest.mark.asyncio
async def test_two_executors_are_independent() -> None:
    delegate_calls: list[tuple[str, str, dict[str, object]]] = []

    async def delegate(
        call_id: str, tool_name: str, args: dict[str, object]
    ) -> DelegatedToolResult:
        delegate_calls.append((call_id, tool_name, args))
        return DelegatedToolResult(
            call_id=call_id,
            name=tool_name,
            output=f"delegated:{args['value']}",
        )

    delegated_executor = ToolExecutor(
        [LocalTool()],
        delegate=delegate,
        delegated_tool_names=frozenset({"delegated_action"}),
    )
    local_executor = ToolExecutor([LocalTool()])

    delegated_result, local_result = await asyncio.gather(
        delegated_executor.execute(
            ToolCall(id="call-3", name="delegated_action", arguments={"value": 11})
        ),
        local_executor.execute(
            ToolCall(id="call-4", name="delegated_action", arguments={"value": 11})
        ),
    )

    assert _extract_message(delegated_result.output) == "delegated:11"
    assert _extract_message(local_result.output) == "local:11"
    assert delegate_calls == [("call-3", "delegated_action", {"value": 11})]


def test_executor_delegation_is_constructor_only() -> None:
    executor = ToolExecutor([LocalTool()])

    assert not hasattr(executor, "set_delegation")
    assert not hasattr(executor, "clear_delegation")


@pytest.mark.asyncio
async def test_executor_non_delegated_tool_runs_locally() -> None:
    delegate_calls: list[tuple[str, str, dict[str, object]]] = []

    async def delegate(
        call_id: str, tool_name: str, args: dict[str, object]
    ) -> DelegatedToolResult:
        delegate_calls.append((call_id, tool_name, args))
        return DelegatedToolResult(call_id=call_id, name=tool_name, output="delegated")

    executor = ToolExecutor(
        [LocalTool()],
        delegate=delegate,
        delegated_tool_names=frozenset({"other_tool"}),
    )

    result = await executor.execute(
        ToolCall(id="call-5", name="delegated_action", arguments={"value": 3})
    )

    assert _extract_message(result.output) == "local:3"
    assert delegate_calls == []


@pytest.mark.asyncio
async def test_runtime_invoke_uses_per_call_tool_executor() -> None:
    adapter = QueueAdapter(
        [
            StepExecutionResult(
                tool_calls=(
                    ToolCall(
                        id="call-6",
                        name="reply",
                        arguments={"message": "ignored"},
                    ),
                )
            )
        ]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[ReplyTool()],
        tool_rules=[TerminalToolRule(tool_name="reply")],
        max_steps=2,
    )
    override_executor = RecordingExecutor([ReplyTool()], "override invoke")

    result = await runtime.invoke(
        "hello",
        user_id=1,
        history=[],
        tool_executor=override_executor,
    )

    assert result.response == "override invoke"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert override_executor.calls == [("reply", True)]


@pytest.mark.asyncio
async def test_runtime_coerced_tool_calls_use_per_call_tool_executor() -> None:
    adapter = QueueAdapter(
        [StepExecutionResult(assistant_text='send_message({"message": "ignored"})')]
    )
    runtime = AgentRuntime(
        adapter=adapter,
        tools=[SendMessageTool()],
        tool_rules=[TerminalToolRule(tool_name="send_message")],
        max_steps=2,
    )
    override_executor = RecordingExecutor([SendMessageTool()], "override coerced")

    result = await runtime.invoke(
        "hello",
        user_id=1,
        history=[],
        tool_executor=override_executor,
    )

    assert result.response == "override coerced"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert override_executor.calls == [("send_message", True)]


@pytest.mark.asyncio
async def test_runtime_resume_after_approval_uses_per_call_tool_executor() -> None:
    runtime = AgentRuntime(
        adapter=QueueAdapter([]),
        tools=[ReplyTool()],
        tool_rules=[TerminalToolRule(tool_name="reply")],
        max_steps=2,
    )
    override_executor = RecordingExecutor([ReplyTool()], "override approval")

    result = await runtime.resume_after_approval(
        approved=True,
        tool_call=ToolCall(id="call-7", name="reply", arguments={"message": "ignored"}),
        user_id=1,
        history=[],
        tool_executor=override_executor,
    )

    assert result.response == "override approval"
    assert result.stop_reason == StopReason.TERMINAL_TOOL.value
    assert override_executor.calls == [("reply", True)]
