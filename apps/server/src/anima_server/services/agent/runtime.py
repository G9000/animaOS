from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from anima_server.config import settings
from anima_server.services.agent.adapters import build_adapter
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.executor import ToolExecutor
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.messages import (
    build_conversation_messages,
    make_assistant_message,
    make_tool_message,
    message_content,
)
from anima_server.services.agent.rules import ToolRule, ToolRulesSolver
from anima_server.services.agent.runtime_types import (
    LLMRequest,
    MessageSnapshot,
    StepExecutionResult,
    StepTrace,
    StopReason,
    ToolCall,
    ToolExecutionResult,
)
from anima_server.services.agent.state import AgentResult, StoredMessage
from anima_server.services.agent.streaming import (
    AgentStreamEvent,
    build_chunk_event,
    build_tool_call_event,
    build_tool_return_event,
)
from anima_server.services.agent.prompt_budget import apply_prompt_budget
from anima_server.services.agent.system_prompt import SystemPromptContext, build_system_prompt
from anima_server.services.agent.tools import get_tool_rules, get_tool_summaries, get_tools

StreamEventCallback = Callable[[AgentStreamEvent], Awaitable[None]]


class AgentRuntime:
    """Async loop runtime used for orchestration."""

    def __init__(
        self,
        *,
        adapter: BaseLLMAdapter,
        tools: Sequence[Any] = (),
        tool_rules: Sequence[ToolRule] = (),
        persona_template: str = "default",
        tool_summaries: Sequence[str] = (),
        tool_executor: ToolExecutor | None = None,
        max_steps: int = 4,
    ) -> None:
        self._adapter = adapter
        self._tool_registry = {
            tool_name: tool for tool in tools if (tool_name := _tool_name(tool))
        }
        self._tool_names = tuple(self._tool_registry)
        self._tool_rules = tuple(tool_rules)
        self._persona_template = persona_template
        self._tool_summaries = tuple(tool_summaries)
        self._tool_executor = tool_executor or ToolExecutor(list(self._tool_registry.values()))
        self._max_steps = max_steps

    def prepare_system_prompt(self) -> str:
        return self.build_system_prompt()

    def build_system_prompt(
        self,
        *,
        memory_blocks: Sequence[MemoryBlock] = (),
    ) -> str:
        self._adapter.prepare()
        budgeted_blocks = apply_prompt_budget(memory_blocks)
        return build_system_prompt(
            SystemPromptContext(
                persona_template=self._persona_template,
                tool_summaries=self._tool_summaries,
                memory_blocks=budgeted_blocks,
            )
        )

    async def invoke(
        self,
        user_message: str,
        user_id: int,
        history: list[StoredMessage],
        *,
        conversation_turn_count: int | None = None,
        memory_blocks: Sequence[MemoryBlock] = (),
        event_callback: StreamEventCallback | None = None,
    ) -> AgentResult:
        system_prompt = self.build_system_prompt(memory_blocks=memory_blocks)
        messages = build_conversation_messages(
            history,
            user_message,
            system_prompt=system_prompt,
        )
        stop_reason = StopReason.END_TURN
        response = ""
        tools_used: list[str] = []
        step_traces: list[StepTrace] = []
        rules_solver = ToolRulesSolver(self._tool_rules)

        for step_index in range(self._max_steps):
            request_messages = _snapshot_messages(messages)
            allowed_tool_names = tuple(
                sorted(rules_solver.get_allowed_tools(self._tool_names))
            )
            force_tool_call = bool(allowed_tool_names) and (
                rules_solver.should_force_tool_call()
                or "send_message" in allowed_tool_names
            )
            step_result, streamed_assistant_text = await self._run_step(
                messages=messages,
                user_id=user_id,
                conversation_turn_count=conversation_turn_count,
                step_index=step_index,
                system_prompt=system_prompt,
                allowed_tool_names=allowed_tool_names,
                force_tool_call=force_tool_call,
                event_callback=event_callback,
            )
            tool_results: list[ToolExecutionResult] = []
            terminal_tool_hit = False
            awaiting_approval = False
            rule_violation_hit = False

            if not step_result.tool_calls:
                synthetic_terminal_tool = await self._coerce_terminal_send_message(
                    step_result=step_result,
                    allowed_tool_names=allowed_tool_names,
                    step_index=step_index,
                    event_callback=event_callback,
                )
                if synthetic_terminal_tool is not None:
                    synthetic_tool_call, synthetic_tool_result = synthetic_terminal_tool
                    if synthetic_tool_call.name not in tools_used:
                        tools_used.append(synthetic_tool_call.name)
                    response = synthetic_tool_result.output or response
                    step_traces.append(
                        StepTrace(
                            step_index=step_index,
                            request_messages=request_messages,
                            allowed_tools=allowed_tool_names,
                            force_tool_call=force_tool_call,
                            assistant_text=step_result.assistant_text,
                            tool_calls=(synthetic_tool_call,),
                            tool_results=(synthetic_tool_result,),
                            usage=step_result.usage,
                        )
                    )
                    stop_reason = StopReason.TERMINAL_TOOL
                    break

                response = step_result.assistant_text or response
                if (
                    event_callback is not None
                    and step_result.assistant_text
                    and not streamed_assistant_text
                ):
                    await event_callback(build_chunk_event(step_result.assistant_text))
                step_traces.append(
                    StepTrace(
                        step_index=step_index,
                        request_messages=request_messages,
                        allowed_tools=allowed_tool_names,
                        force_tool_call=force_tool_call,
                        assistant_text=step_result.assistant_text,
                        tool_calls=step_result.tool_calls,
                        usage=step_result.usage,
                    )
                )
                stop_reason = StopReason.END_TURN
                break

            if event_callback is not None:
                for tool_call in step_result.tool_calls:
                    await event_callback(build_tool_call_event(step_index, tool_call))

            for tool_call in step_result.tool_calls:
                violation = rules_solver.validate_tool_call(
                    tool_call.name,
                    self._tool_names,
                )
                if violation is not None:
                    tool_result = ToolExecutionResult(
                        call_id=tool_call.id,
                        name=tool_call.name,
                        output=f"Tool rule violation: {violation}",
                        is_error=True,
                    )
                    tool_results.append(tool_result)
                    messages.append(
                        make_tool_message(
                            tool_result.output,
                            tool_call_id=tool_result.call_id,
                            name=tool_result.name,
                        )
                    )
                    if event_callback is not None:
                        await event_callback(
                            build_tool_return_event(step_index, tool_result)
                        )
                    rule_violation_hit = True
                    break

                if rules_solver.requires_approval(tool_call.name):
                    tool_result = ToolExecutionResult(
                        call_id=tool_call.id,
                        name=tool_call.name,
                        output=f"Approval required before running tool: {tool_call.name}",
                        is_error=True,
                    )
                    tool_results.append(tool_result)
                    messages.append(
                        make_tool_message(
                            tool_result.output,
                            tool_call_id=tool_result.call_id,
                            name=tool_result.name,
                        )
                    )
                    if event_callback is not None:
                        await event_callback(
                            build_tool_return_event(step_index, tool_result)
                        )
                    stop_reason = StopReason.AWAITING_APPROVAL
                    awaiting_approval = True
                    break

                tool_result = await self._tool_executor.execute(
                    tool_call,
                    is_terminal=rules_solver.is_terminal(tool_call.name),
                )
                tool_results.append(tool_result)
                messages.append(
                    make_tool_message(
                        tool_result.output,
                        tool_call_id=tool_result.call_id,
                        name=tool_result.name,
                    )
                )
                if event_callback is not None:
                    await event_callback(build_tool_return_event(step_index, tool_result))
                rules_solver.update_state(tool_call.name, tool_result.output)
                if tool_call.name not in tools_used:
                    tools_used.append(tool_call.name)
                if tool_result.is_terminal:
                    response = tool_result.output or response
                    stop_reason = StopReason.TERMINAL_TOOL
                    terminal_tool_hit = True
                    break

            step_traces.append(
                StepTrace(
                    step_index=step_index,
                    request_messages=request_messages,
                    allowed_tools=allowed_tool_names,
                    force_tool_call=force_tool_call,
                    assistant_text=step_result.assistant_text,
                    tool_calls=step_result.tool_calls,
                    tool_results=tuple(tool_results),
                    usage=step_result.usage,
                )
            )

            if rule_violation_hit:
                continue

            if not terminal_tool_hit and not awaiting_approval:
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
            stop_reason=stop_reason.value,
            tools_used=tools_used,
            step_traces=step_traces,
        )

    async def _run_step(
        self,
        *,
        messages: list[object],
        user_id: int,
        conversation_turn_count: int | None,
        step_index: int,
        system_prompt: str,
        allowed_tool_names: Sequence[str],
        force_tool_call: bool,
        event_callback: StreamEventCallback | None = None,
    ) -> tuple[StepExecutionResult, bool]:
        request = LLMRequest(
            messages=tuple(messages),
            user_id=user_id,
            conversation_turn_count=conversation_turn_count,
            step_index=step_index,
            max_steps=self._max_steps,
            system_prompt=system_prompt,
            available_tools=tuple(
                self._tool_registry[name]
                for name in allowed_tool_names
                if name in self._tool_registry
            ),
            force_tool_call=force_tool_call,
        )
        streamed_assistant_text = False

        if event_callback is None:
            step_result = await self._adapter.invoke(request)
        else:
            step_result = None
            async for stream_event in self._adapter.stream(request):
                if stream_event.content_delta:
                    streamed_assistant_text = True
                    await event_callback(build_chunk_event(stream_event.content_delta))
                if stream_event.result is not None:
                    step_result = stream_event.result

            if step_result is None:
                raise RuntimeError("Adapter stream ended without a final step result.")

        if step_result.assistant_text or step_result.tool_calls:
            messages.append(
                make_assistant_message(
                    step_result.assistant_text,
                    tool_calls=step_result.tool_calls,
                )
            )

        return step_result, streamed_assistant_text

    async def _coerce_terminal_send_message(
        self,
        *,
        step_result: StepExecutionResult,
        allowed_tool_names: Sequence[str],
        step_index: int,
        event_callback: StreamEventCallback | None,
    ) -> tuple[ToolCall, ToolExecutionResult] | None:
        if not step_result.assistant_text.strip():
            return None
        if "send_message" not in allowed_tool_names:
            return None
        if "send_message" not in self._tool_registry:
            return None

        tool_call = ToolCall(
            id=f"synthetic-send-message-{step_index}",
            name="send_message",
            arguments={"message": step_result.assistant_text},
        )
        tool_result = await self._tool_executor.execute(
            tool_call,
            is_terminal=True,
        )
        if event_callback is not None:
            await event_callback(build_tool_call_event(step_index, tool_call))
            await event_callback(build_tool_return_event(step_index, tool_result))
        return tool_call, tool_result


def build_loop_runtime() -> AgentRuntime:
    tools = get_tools()
    return AgentRuntime(
        adapter=build_adapter(),
        tools=tools,
        tool_rules=get_tool_rules(tools),
        persona_template=settings.agent_persona_template,
        tool_summaries=get_tool_summaries(tools),
        tool_executor=ToolExecutor(tools),
        max_steps=max(1, settings.agent_max_steps),
    )


def _default_response(stop_reason: StopReason) -> str:
    if stop_reason == StopReason.MAX_STEPS:
        return "Agent runtime reached the maximum step limit without a final response."
    if stop_reason == StopReason.AWAITING_APPROVAL:
        return "Agent runtime is waiting for approval before running a tool."
    return ""


def _snapshot_messages(messages: list[object]) -> tuple[MessageSnapshot, ...]:
    snapshots: list[MessageSnapshot] = []

    for message in messages:
        message_type = getattr(message, "type", "")
        if message_type == "ai":
            role = "assistant"
        elif message_type == "tool":
            role = "tool"
        elif message_type == "system":
            role = "system"
        else:
            role = "user"

        snapshots.append(
            MessageSnapshot(
                role=role,
                content=message_content(message),
                tool_name=getattr(message, "name", None),
                tool_call_id=getattr(message, "tool_call_id", None),
                tool_calls=_snapshot_tool_calls(getattr(message, "tool_calls", ())),
            )
        )

    return tuple(snapshots)


def _snapshot_tool_calls(raw_tool_calls: object) -> tuple[ToolCall, ...]:
    if not isinstance(raw_tool_calls, list):
        return ()

    tool_calls: list[ToolCall] = []
    for index, raw_tool_call in enumerate(raw_tool_calls):
        if isinstance(raw_tool_call, dict):
            name = str(raw_tool_call.get("name", "")).strip()
            call_id = str(raw_tool_call.get("id") or f"tool-call-{index}")
            arguments = raw_tool_call.get("args", {})
            parse_error = raw_tool_call.get("parse_error")
            raw_arguments = raw_tool_call.get("raw_arguments")
        else:
            name = str(getattr(raw_tool_call, "name", "")).strip()
            call_id = str(getattr(raw_tool_call, "id", None) or f"tool-call-{index}")
            arguments = getattr(raw_tool_call, "args", {})
            parse_error = getattr(raw_tool_call, "parse_error", None)
            raw_arguments = getattr(raw_tool_call, "raw_arguments", None)

        if not name:
            continue

        tool_calls.append(
            ToolCall(
                id=call_id,
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
                parse_error=(
                    str(parse_error).strip()
                    if isinstance(parse_error, str) and parse_error.strip()
                    else None
                ),
                raw_arguments=(
                    str(raw_arguments)[:500]
                    if isinstance(raw_arguments, str) and raw_arguments
                    else None
                ),
            )
        )

    return tuple(tool_calls)


def _tool_name(tool: Any) -> str:
    return getattr(tool, "name", "") or getattr(tool, "__name__", "")
