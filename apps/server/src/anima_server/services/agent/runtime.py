from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import AIMessage, ToolMessage

from anima_server.config import settings
from anima_server.services.agent.adapters import build_adapter
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.executor import ToolExecutor
from anima_server.services.agent.messages import build_conversation_messages
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    StepExecutionResult,
    StopReason,
    ToolCall,
)
from anima_server.services.agent.state import AgentResult, StoredMessage
from anima_server.services.agent.system_prompt import SystemPromptContext, build_system_prompt
from anima_server.services.agent.tools import get_tool_summaries, get_tools


class AgentRuntime:
    """Async loop runtime used for orchestration."""

    def __init__(
        self,
        *,
        adapter: BaseLLMAdapter,
        persona_template: str = "default",
        tool_summaries: Sequence[str] = (),
        tool_executor: ToolExecutor | None = None,
        max_steps: int = 4,
    ) -> None:
        self._adapter = adapter
        self._persona_template = persona_template
        self._tool_summaries = tuple(tool_summaries)
        self._tool_executor = tool_executor or ToolExecutor([])
        self._max_steps = max_steps

    def prepare_system_prompt(self) -> str:
        self._adapter.prepare()
        return build_system_prompt(
            SystemPromptContext(
                persona_template=self._persona_template,
                tool_summaries=self._tool_summaries,
            )
        )

    async def invoke(
        self,
        user_message: str,
        user_id: int,
        history: list[StoredMessage],
    ) -> AgentResult:
        system_prompt = self.prepare_system_prompt()
        messages = build_conversation_messages(
            history,
            user_message,
            system_prompt=system_prompt,
        )
        stop_reason = StopReason.END_TURN
        response = ""
        tools_used: list[str] = []

        for step_index in range(self._max_steps):
            step_result = await self._run_step(
                messages=messages,
                user_id=user_id,
                step_index=step_index,
                system_prompt=system_prompt,
            )
            response = step_result.assistant_text or response

            if not step_result.tool_calls:
                stop_reason = StopReason.END_TURN
                break

            for tool_call in step_result.tool_calls:
                if tool_call.name not in tools_used:
                    tools_used.append(tool_call.name)

                tool_result = await self._tool_executor.execute(tool_call)
                messages.append(
                    ToolMessage(
                        content=tool_result.output,
                        tool_call_id=tool_result.call_id,
                        name=tool_result.name,
                    )
                )
                if tool_result.is_terminal:
                    stop_reason = StopReason.TERMINAL_TOOL
                    break
            else:
                continue

            break
        else:
            stop_reason = StopReason.MAX_STEPS

        if not response:
            response = _default_response(stop_reason)

        return AgentResult(
            response=response,
            model=self._adapter.model,
            provider=self._adapter.provider,
            tools_used=tools_used,
        )

    async def _run_step(
        self,
        *,
        messages: list[object],
        user_id: int,
        step_index: int,
        system_prompt: str,
    ) -> StepExecutionResult:
        step_result = await self._adapter.invoke(
            LLMRequest(
                messages=tuple(messages),
                user_id=user_id,
                step_index=step_index,
                max_steps=self._max_steps,
                system_prompt=system_prompt,
            )
        )

        if step_result.assistant_text or step_result.tool_calls:
            messages.append(
                AIMessage(
                    content=step_result.assistant_text,
                    tool_calls=[
                        _to_langchain_tool_call(tool_call)
                        for tool_call in step_result.tool_calls
                    ],
                )
            )

        return step_result


def build_loop_runtime() -> AgentRuntime:
    tools = get_tools()
    return AgentRuntime(
        adapter=build_adapter(),
        persona_template=settings.agent_persona_template,
        tool_summaries=get_tool_summaries(tools),
        tool_executor=ToolExecutor(tools),
        max_steps=max(1, settings.agent_max_steps),
    )


def _default_response(stop_reason: StopReason) -> str:
    if stop_reason == StopReason.MAX_STEPS:
        return "Agent runtime reached the maximum step limit without a final response."
    return ""


def _to_langchain_tool_call(tool_call: ToolCall) -> dict[str, object]:
    return {
        "id": tool_call.id,
        "name": tool_call.name,
        "args": dict(tool_call.arguments),
        "type": "tool_call",
    }
