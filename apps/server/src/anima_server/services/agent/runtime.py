from __future__ import annotations
from anima_server.services.agent.tools import get_tool_rules, get_tool_summaries, get_tools
from anima_server.services.agent.system_prompt import (
    SystemPromptContext,
    build_system_prompt,
    split_prompt_memory_blocks,
)
from anima_server.services.agent.prompt_budget import PromptBudgetTrace, plan_prompt_budget
from anima_server.services.agent.streaming import (
    AgentStreamEvent,
    build_chunk_event,
    build_reasoning_event,
    build_timing_event,
    build_tool_call_event,
    build_tool_return_event,
)
from anima_server.services.agent.state import AgentResult, StoredMessage
from anima_server.services.agent.runtime_types import (
    DryRunResult,
    LLMRequest,
    MessageSnapshot,
    StepContext,
    StepExecutionResult,
    StepFailedError,
    StepProgression,
    StepTiming,
    StepTrace,
    StopReason,
    ToolCall,
    ToolExecutionResult,
)
from anima_server.services.agent.rules import ToolRule, ToolRulesSolver
from anima_server.services.agent.messages import (
    build_conversation_messages,
    make_assistant_message,
    make_tool_message,
    message_content,
)
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.executor import ToolExecutor
from anima_server.services.agent.compaction import estimate_message_tokens
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.adapters import build_adapter
from anima_server.config import settings
from anima_server.services.agent.llm import (
    ContextWindowOverflowError,
    LLMInvocationError,
)

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any

logger = logging.getLogger(__name__)


def _is_retryable_error(exc: Exception) -> bool:
    """Return True if the exception is transient and worth retrying."""
    if isinstance(exc, ContextWindowOverflowError):
        return False
    if isinstance(exc, asyncio.TimeoutError):
        return True
    if isinstance(exc, LLMInvocationError):
        msg = str(exc).lower()
        # Rate limits and server errors are retryable
        for pattern in ("429", "500", "502", "503", "504", "rate limit",
                        "overloaded", "temporarily unavailable", "try again"):
            if pattern in msg:
                return True
    if isinstance(exc, (ConnectionError, OSError)):
        return True
    return False


StreamEventCallback = Callable[[AgentStreamEvent], Awaitable[None]]
MemoryRefresher = Callable[[], Awaitable[tuple[MemoryBlock, ...] | None]]


class _CancelledDuringStream(Exception):
    """Raised inside _run_step when the cancel event fires mid-stream."""


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
        if self._tool_rules:
            ToolRulesSolver(self._tool_rules).warn_unknown_tools(
                self._tool_names)
        self._persona_template = persona_template
        self._tool_summaries = tuple(tool_summaries)
        self._tool_executor = tool_executor or ToolExecutor(
            list(self._tool_registry.values()))
        self._max_steps = max_steps

    def prepare_system_prompt(self) -> str:
        return self.build_system_prompt()

    def build_system_prompt(
        self,
        *,
        memory_blocks: Sequence[MemoryBlock] = (),
    ) -> str:
        system_prompt, _prompt_budget = self.build_system_prompt_with_budget(
            memory_blocks=memory_blocks
        )
        return system_prompt

    def build_system_prompt_with_budget(
        self,
        *,
        memory_blocks: Sequence[MemoryBlock] = (),
    ) -> tuple[str, PromptBudgetTrace | None]:
        self._adapter.prepare()
        dynamic_identity, persona_content, prompt_memory_blocks = split_prompt_memory_blocks(
            memory_blocks)
        budget_plan = plan_prompt_budget(prompt_memory_blocks)
        system_prompt = build_system_prompt(
            SystemPromptContext(
                persona_template=self._persona_template,
                persona_content=persona_content,
                tool_summaries=self._tool_summaries,
                memory_blocks=budget_plan.blocks,
                dynamic_identity=dynamic_identity,
            )
        )
        prompt_budget = replace(
            budget_plan.trace,
            dynamic_identity_chars=len(dynamic_identity),
            dynamic_identity_token_estimate=estimate_message_tokens(
                content_text=dynamic_identity,
                content_json=None,
                tool_name=None,
            ),
            system_prompt_chars=len(system_prompt),
            system_prompt_token_estimate=estimate_message_tokens(
                content_text=system_prompt,
                content_json=None,
                tool_name=None,
            ),
        )
        return system_prompt, prompt_budget

    async def invoke(
        self,
        user_message: str,
        user_id: int,
        history: list[StoredMessage],
        *,
        conversation_turn_count: int | None = None,
        memory_blocks: Sequence[MemoryBlock] = (),
        event_callback: StreamEventCallback | None = None,
        dry_run: bool = False,
        cancel_event: asyncio.Event | None = None,
        memory_refresher: MemoryRefresher | None = None,
    ) -> AgentResult | DryRunResult:
        system_prompt, prompt_budget = self.build_system_prompt_with_budget(
            memory_blocks=memory_blocks,
        )
        messages = build_conversation_messages(
            history,
            user_message,
            system_prompt=system_prompt,
        )

        rules_solver = ToolRulesSolver(self._tool_rules)
        allowed_tool_names = tuple(
            sorted(rules_solver.get_allowed_tools(self._tool_names))
        )

        # --- Dry-run: return prompt assembly without side effects ---
        if dry_run:
            request_messages = _snapshot_messages(messages)
            tool_schemas = tuple(
                _tool_schema(self._tool_registry[name])
                for name in allowed_tool_names
                if name in self._tool_registry
            )
            estimated_tokens = estimate_message_tokens(
                content_text=system_prompt,
                content_json=None,
                tool_name=None,
            )
            for snap in request_messages:
                estimated_tokens += estimate_message_tokens(
                    content_text=snap.content,
                    content_json=None,
                    tool_name=snap.tool_name,
                )
            return DryRunResult(
                system_prompt=system_prompt,
                messages=request_messages,
                tool_schemas=tool_schemas,
                allowed_tools=allowed_tool_names,
                memory_blocks=tuple(memory_blocks),
                estimated_prompt_tokens=estimated_tokens,
                prompt_budget=prompt_budget,
            )

        # --- Normal execution ---
        stop_reason = StopReason.END_TURN
        response = ""
        tools_used: list[str] = []
        step_traces: list[StepTrace] = []

        for step_index in range(self._max_steps):
            # --- Cancellation check (step boundary) ---
            if cancel_event is not None and cancel_event.is_set():
                stop_reason = StopReason.CANCELLED
                break

            request_messages = _snapshot_messages(messages)
            allowed_tool_names = tuple(
                sorted(rules_solver.get_allowed_tools(self._tool_names))
            )
            force_tool_call = bool(allowed_tool_names) and (
                rules_solver.should_force_tool_call()
                or "send_message" in allowed_tool_names
            )
            try:
                step_result, streamed_assistant_text, step_ctx = await self._run_step(
                    messages=messages,
                    user_id=user_id,
                    conversation_turn_count=conversation_turn_count,
                    step_index=step_index,
                    system_prompt=system_prompt,
                    allowed_tool_names=allowed_tool_names,
                    force_tool_call=force_tool_call,
                    event_callback=event_callback,
                    cancel_event=cancel_event,
                )
            except _CancelledDuringStream:
                stop_reason = StopReason.CANCELLED
                break
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
                    step_ctx.progression = StepProgression.TOOLS_COMPLETED
                    step_traces.append(
                        _build_step_trace(
                            step_ctx, step_result, request_messages,
                            allowed_tool_names, force_tool_call,
                            tool_calls=(synthetic_tool_call,),
                            tool_results=(synthetic_tool_result,),
                        )
                    )
                    if event_callback is not None:
                        await event_callback(
                            build_timing_event(step_index, _compute_timing(step_ctx)))
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
                    _build_step_trace(
                        step_ctx, step_result, request_messages,
                        allowed_tool_names, force_tool_call,
                    )
                )
                if event_callback is not None:
                    await event_callback(
                        build_timing_event(step_index, _compute_timing(step_ctx)))
                stop_reason = StopReason.END_TURN
                break

            if event_callback is not None:
                for tool_call in step_result.tool_calls:
                    await event_callback(build_tool_call_event(step_index, tool_call))

            for tc_index, tool_call in enumerate(step_result.tool_calls):
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

                if tc_index == 0:
                    step_ctx.progression = StepProgression.TOOLS_STARTED
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

            if tool_results:
                step_ctx.progression = StepProgression.TOOLS_COMPLETED
            step_traces.append(
                _build_step_trace(
                    step_ctx, step_result, request_messages,
                    allowed_tool_names, force_tool_call,
                    tool_results=tuple(tool_results),
                )
            )
            if event_callback is not None:
                await event_callback(
                    build_timing_event(step_index, _compute_timing(step_ctx)))

            if rule_violation_hit:
                continue

            # Refresh memory blocks between steps if a tool modified memory.
            if (
                memory_refresher is not None
                and not terminal_tool_hit
                and not awaiting_approval
                and any(tr.memory_modified for tr in tool_results)
            ):
                try:
                    fresh_blocks = await memory_refresher()
                    if fresh_blocks is not None:
                        system_prompt, prompt_budget = self.build_system_prompt_with_budget(
                            memory_blocks=fresh_blocks,
                        )
                        # Replace the system message (always first in the list).
                        if messages and hasattr(messages[0], "type") and getattr(messages[0], "type", "") == "system":
                            from anima_server.services.agent.messages import make_system_message
                            messages[0] = make_system_message(system_prompt)
                except Exception:  # noqa: BLE001
                    logger.debug("Memory refresh between steps failed", exc_info=True)

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
            prompt_budget=prompt_budget,
        )

    # ------------------------------------------------------------------
    # Approval re-entry
    # ------------------------------------------------------------------

    async def resume_after_approval(
        self,
        *,
        approved: bool,
        tool_call: ToolCall,
        user_id: int,
        history: list[StoredMessage],
        denial_reason: str | None = None,
        memory_blocks: Sequence[MemoryBlock] = (),
        conversation_turn_count: int | None = None,
        event_callback: StreamEventCallback | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AgentResult:
        """Resume a turn after an approval decision.

        On approve: execute the tool directly (no LLM call).  If the tool
        is terminal, return immediately.  Otherwise make one follow-up LLM
        call so the companion can respond.

        On deny: inject a tool-error result with the denial reason and make
        one LLM call so the companion can acknowledge the denial.
        """
        system_prompt, prompt_budget = self.build_system_prompt_with_budget(
            memory_blocks=memory_blocks,
        )
        messages = build_conversation_messages(
            history,
            user_message=None,
            system_prompt=system_prompt,
        )
        rules_solver = ToolRulesSolver(self._tool_rules)

        step_traces: list[StepTrace] = []
        tools_used: list[str] = []
        response = ""

        # Step 0: execute (or deny) the pending tool call
        step_ctx = StepContext(
            step_index=0,
            progression=StepProgression.TOOLS_STARTED,
            start_time=time.monotonic(),
        )

        if approved:
            tool_result = await self._tool_executor.execute(
                tool_call,
                is_terminal=rules_solver.is_terminal(tool_call.name),
            )
        else:
            reason = denial_reason or "No reason provided"
            tool_result = ToolExecutionResult(
                call_id=tool_call.id,
                name=tool_call.name,
                output=f"Tool {tool_call.name} was denied by user. Reason: {reason}",
                is_error=True,
            )

        if tool_call.name not in tools_used:
            tools_used.append(tool_call.name)
        messages.append(
            make_tool_message(
                tool_result.output,
                tool_call_id=tool_result.call_id,
                name=tool_result.name,
            )
        )
        if event_callback is not None:
            await event_callback(build_tool_call_event(0, tool_call))
            await event_callback(build_tool_return_event(0, tool_result))

        step_ctx.progression = StepProgression.TOOLS_COMPLETED
        request_messages = _snapshot_messages(messages)
        allowed_tool_names = tuple(
            sorted(rules_solver.get_allowed_tools(self._tool_names))
        )
        step_traces.append(
            _build_step_trace(
                step_ctx,
                StepExecutionResult(),  # no LLM result for this step
                request_messages,
                allowed_tool_names,
                False,
                tool_calls=(tool_call,),
                tool_results=(tool_result,),
            )
        )
        if event_callback is not None:
            await event_callback(
                build_timing_event(0, _compute_timing(step_ctx)))

        # If the tool is terminal and was approved, return immediately.
        if approved and tool_result.is_terminal:
            response = tool_result.output or response
            return AgentResult(
                response=response,
                model=self._adapter.model,
                provider=self._adapter.provider,
                stop_reason=StopReason.TERMINAL_TOOL.value,
                tools_used=tools_used,
                step_traces=step_traces,
                prompt_budget=prompt_budget,
            )

        # Step 1: one LLM follow-up so the companion can respond
        if cancel_event is not None and cancel_event.is_set():
            return AgentResult(
                response="",
                model=self._adapter.model,
                provider=self._adapter.provider,
                stop_reason=StopReason.CANCELLED.value,
                tools_used=tools_used,
                step_traces=step_traces,
                prompt_budget=prompt_budget,
            )

        allowed_tool_names = tuple(
            sorted(rules_solver.get_allowed_tools(self._tool_names))
        )
        force_tool_call = bool(allowed_tool_names) and (
            rules_solver.should_force_tool_call()
            or "send_message" in allowed_tool_names
        )
        try:
            step_result, streamed_assistant_text, follow_ctx = await self._run_step(
                messages=messages,
                user_id=user_id,
                conversation_turn_count=conversation_turn_count,
                step_index=1,
                system_prompt=system_prompt,
                allowed_tool_names=allowed_tool_names,
                force_tool_call=force_tool_call,
                event_callback=event_callback,
                cancel_event=cancel_event,
            )
        except _CancelledDuringStream:
            return AgentResult(
                response="",
                model=self._adapter.model,
                provider=self._adapter.provider,
                stop_reason=StopReason.CANCELLED.value,
                tools_used=tools_used,
                step_traces=step_traces,
                prompt_budget=prompt_budget,
            )

        response = step_result.assistant_text or response
        if (
            event_callback is not None
            and step_result.assistant_text
            and not streamed_assistant_text
        ):
            await event_callback(build_chunk_event(step_result.assistant_text))

        follow_request_messages = _snapshot_messages(messages)
        step_traces.append(
            _build_step_trace(
                follow_ctx, step_result, follow_request_messages,
                allowed_tool_names, force_tool_call,
            )
        )
        if event_callback is not None:
            await event_callback(
                build_timing_event(1, _compute_timing(follow_ctx)))

        if not response:
            response = _default_response(StopReason.END_TURN)

        return AgentResult(
            response=response,
            model=self._adapter.model,
            provider=self._adapter.provider,
            stop_reason=StopReason.END_TURN.value,
            tools_used=tools_used,
            step_traces=step_traces,
            prompt_budget=prompt_budget,
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
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[StepExecutionResult, bool, StepContext]:
        ctx = StepContext(
            step_index=step_index,
            progression=StepProgression.START,
            start_time=time.monotonic(),
        )
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

        retry_limit = max(0, settings.agent_llm_retry_limit)
        backoff_factor = settings.agent_llm_retry_backoff_factor
        max_delay = settings.agent_llm_retry_max_delay

        try:
            step_result = await self._invoke_llm_with_retry(
                ctx=ctx,
                request=request,
                event_callback=event_callback,
                cancel_event=cancel_event,
                retry_limit=retry_limit,
                backoff_factor=backoff_factor,
                max_delay=max_delay,
            )

            # Track whether we streamed content to the client.
            if event_callback is not None and step_result.assistant_text:
                streamed_assistant_text = True

            # Emit reasoning event before any chunk/tool events for this step.
            if event_callback is not None and step_result.reasoning_content:
                await event_callback(
                    build_reasoning_event(
                        step_index,
                        step_result.reasoning_content,
                        step_result.reasoning_signature,
                    )
                )

            if step_result.assistant_text or step_result.tool_calls:
                messages.append(
                    make_assistant_message(
                        step_result.assistant_text,
                        tool_calls=step_result.tool_calls,
                    )
                )
        except _CancelledDuringStream:
            raise
        except Exception as exc:
            raise StepFailedError(exc, ctx) from exc

        return step_result, streamed_assistant_text, ctx

    async def _invoke_llm_with_retry(
        self,
        *,
        ctx: StepContext,
        request: LLMRequest,
        event_callback: StreamEventCallback | None,
        cancel_event: asyncio.Event | None,
        retry_limit: int,
        backoff_factor: float,
        max_delay: float,
    ) -> StepExecutionResult:
        """Call the adapter with exponential backoff for transient errors.

        Non-retryable errors (context overflow, config errors, auth failures)
        are raised immediately.  Transient errors (timeouts, rate limits,
        server errors) are retried up to *retry_limit* times.
        """
        last_exc: Exception | None = None
        content_streamed = False

        for attempt in range(1, retry_limit + 2):  # attempt 1 .. retry_limit+1
            try:
                timeout = settings.agent_llm_timeout
                if event_callback is None:
                    step_result = await asyncio.wait_for(
                        self._adapter.invoke(request), timeout=timeout,
                    )
                    ctx.llm_end_time = time.monotonic()
                    ctx.progression = StepProgression.RESPONSE_RECEIVED
                else:
                    step_result = None
                    async for stream_event in self._adapter.stream(request):
                        if cancel_event is not None and cancel_event.is_set():
                            raise _CancelledDuringStream()
                        if stream_event.content_delta:
                            if ctx.ttft_time is None:
                                ctx.ttft_time = time.monotonic()
                            content_streamed = True
                            await event_callback(build_chunk_event(stream_event.content_delta))
                        if stream_event.result is not None:
                            step_result = stream_event.result

                    ctx.llm_end_time = time.monotonic()

                    if step_result is None:
                        raise RuntimeError(
                            "Adapter stream ended without a final step result.")

                    ctx.progression = StepProgression.RESPONSE_RECEIVED

                return step_result

            except _CancelledDuringStream:
                raise
            except Exception as exc:
                last_exc = exc
                is_last_attempt = attempt > retry_limit
                # Don't retry if content was already streamed to the client
                # — retrying would cause duplicate/garbled output.
                if content_streamed:
                    raise
                if is_last_attempt or not _is_retryable_error(exc):
                    raise
                delay = min(backoff_factor * (2 ** (attempt - 1)), max_delay)
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s. Retrying in %.1fs",
                    attempt, retry_limit + 1, exc, delay,
                )
                await asyncio.sleep(delay)

        # Should never reach here, but satisfy the type checker.
        assert last_exc is not None
        raise last_exc

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
        tool_summaries=get_tool_summaries(tools),
        tool_executor=ToolExecutor(tools),
        max_steps=max(1, settings.agent_max_steps),
    )


def _default_response(stop_reason: StopReason) -> str:
    if stop_reason == StopReason.MAX_STEPS:
        return "Agent runtime reached the maximum step limit without a final response."
    if stop_reason == StopReason.AWAITING_APPROVAL:
        return "Agent runtime is waiting for approval before running a tool."
    if stop_reason == StopReason.CANCELLED:
        return ""
    return ""


def _compute_timing(ctx: StepContext) -> StepTiming:
    now = time.monotonic()
    return StepTiming(
        step_duration_ms=round((now - ctx.start_time) * 1000, 2)
        if ctx.start_time else None,
        llm_duration_ms=round((ctx.llm_end_time - ctx.start_time) * 1000, 2)
        if ctx.llm_end_time and ctx.start_time else None,
        ttft_ms=round((ctx.ttft_time - ctx.start_time) * 1000, 2)
        if ctx.ttft_time and ctx.start_time else None,
    )


def _build_step_trace(
    ctx: StepContext,
    step_result: StepExecutionResult,
    request_messages: tuple[MessageSnapshot, ...],
    allowed_tool_names: tuple[str, ...],
    force_tool_call: bool,
    *,
    tool_calls: tuple[ToolCall, ...] | None = None,
    tool_results: tuple[ToolExecutionResult, ...] = (),
) -> StepTrace:
    return StepTrace(
        step_index=ctx.step_index,
        request_messages=request_messages,
        allowed_tools=allowed_tool_names,
        force_tool_call=force_tool_call,
        assistant_text=step_result.assistant_text,
        tool_calls=tool_calls if tool_calls is not None else step_result.tool_calls,
        tool_results=tool_results,
        usage=step_result.usage,
        timing=_compute_timing(ctx),
        reasoning_content=step_result.reasoning_content,
        reasoning_signature=step_result.reasoning_signature,
    )


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
                tool_calls=_snapshot_tool_calls(
                    getattr(message, "tool_calls", ())),
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
            call_id = str(getattr(raw_tool_call, "id", None)
                          or f"tool-call-{index}")
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


def _tool_schema(tool: Any) -> dict[str, Any]:
    """Extract a JSON-safe schema dict from a tool for dry-run output."""
    schema: dict[str, Any] = {"name": _tool_name(tool)}
    if hasattr(tool, "description"):
        schema["description"] = tool.description
    if hasattr(tool, "args_schema"):
        try:
            schema["parameters"] = tool.args_schema.model_json_schema()
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to extract schema for tool %s", _tool_name(tool))
    return schema


def _tool_name(tool: Any) -> str:
    return getattr(tool, "name", "") or getattr(tool, "__name__", "")
