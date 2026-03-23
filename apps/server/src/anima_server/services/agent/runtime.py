from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from anima_server.config import settings
from anima_server.services.agent.adapters import build_adapter
from anima_server.services.agent.adapters.base import BaseLLMAdapter
from anima_server.services.agent.compaction import estimate_message_tokens
from anima_server.services.agent.executor import ToolExecutor
from anima_server.services.agent.llm import (
    ContextWindowOverflowError,
    LLMInvocationError,
)
from anima_server.services.agent.memory_blocks import MemoryBlock
from anima_server.services.agent.messages import (
    build_conversation_messages,
    make_assistant_message,
    make_tool_message,
    message_content,
)
from anima_server.services.agent.prompt_budget import PromptBudgetTrace, plan_prompt_budget
from anima_server.services.agent.rules import InitToolRule, ToolRule, ToolRulesSolver
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
from anima_server.services.agent.state import AgentResult, StoredMessage
from anima_server.services.agent.streaming import (
    AgentStreamEvent,
    build_chunk_event,
    build_reasoning_event,
    build_step_request_event,
    build_step_result_event,
    build_thought_event,
    build_timing_event,
    build_tool_call_event,
    build_tool_return_event,
    build_warning_event,
)
from anima_server.services.agent.system_prompt import (
    SystemPromptContext,
    build_system_prompt,
    split_prompt_memory_blocks,
)
from anima_server.services.agent.tools import get_tool_rules, get_tool_summaries, get_tools

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
        for pattern in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "rate limit",
            "overloaded",
            "temporarily unavailable",
            "try again",
        ):
            if pattern in msg:
                return True
    return bool(isinstance(exc, (ConnectionError, OSError)))


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
        self._tool_registry = {tool_name: tool for tool in tools if (tool_name := _tool_name(tool))}
        self._tool_names = tuple(self._tool_registry)
        self._tool_rules = tuple(tool_rules)
        if self._tool_rules:
            ToolRulesSolver(self._tool_rules).warn_unknown_tools(self._tool_names)
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
            memory_blocks
        )
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
        extra_tool_schemas: Sequence[dict[str, Any]] = (),
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
        allowed_tool_names = tuple(sorted(rules_solver.get_allowed_tools(self._tool_names)))

        # --- Dry-run: return prompt assembly without side effects ---
        if dry_run:
            request_messages = _snapshot_messages(messages)
            tool_schemas = tuple(
                _strip_thinking_from_schema(_tool_schema(self._tool_registry[name]))
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
        deferred_tool_calls: list[ToolCall] = []
        last_failed_tool: str | None = None
        _prev_failed_tool: str | None = None

        for step_index in range(self._max_steps):
            # --- Cancellation check (step boundary) ---
            if cancel_event is not None and cancel_event.is_set():
                stop_reason = StopReason.CANCELLED
                break

            request_messages = _snapshot_messages(messages)
            allowed_set = rules_solver.get_allowed_tools(self._tool_names)
            # Action tools from connected clients bypass the rules solver
            # — they are always allowed when registered.
            extra_tool_names = frozenset(
                s.get("function", {}).get("name", "")
                for s in extra_tool_schemas
                if s.get("function", {}).get("name")
            )
            allowed_set = allowed_set | extra_tool_names
            # Exclude a tool only if it failed twice consecutively —
            # give the model one retry chance (matching the sandwich
            # message guidance) before blocking it.
            if (
                last_failed_tool
                and last_failed_tool == _prev_failed_tool
                and last_failed_tool in allowed_set
                and len(allowed_set) > 1
            ):
                allowed_set = allowed_set - {last_failed_tool}
            allowed_tool_names = tuple(sorted(allowed_set))
            force_tool_call = bool(allowed_tool_names) and (
                rules_solver.should_force_tool_call() or "send_message" in allowed_tool_names
            )
            if event_callback is not None:
                await event_callback(
                    build_step_request_event(
                        step_index,
                        request_messages=request_messages,
                        allowed_tools=allowed_tool_names,
                        force_tool_call=force_tool_call,
                    )
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
                    extra_tool_schemas=extra_tool_schemas,
                )
            except _CancelledDuringStream:
                stop_reason = StopReason.CANCELLED
                break
            if event_callback is not None:
                await event_callback(
                    build_step_result_event(
                        step_index,
                        step_result=step_result,
                    )
                )
                if not step_result.assistant_text and not step_result.tool_calls:
                    await event_callback(
                        build_warning_event(
                            step_index,
                            code="empty_step_result",
                            message="LLM returned no assistant text and no tool calls for this step.",
                        )
                    )
                if step_result.reasoning_content:
                    await event_callback(
                        build_reasoning_event(
                            step_index,
                            step_result.reasoning_content,
                            step_result.reasoning_signature,
                        )
                    )
            tool_results: list[ToolExecutionResult] = []
            terminal_tool_hit = False
            awaiting_approval = False
            rule_violation_hit = False

            if not step_result.tool_calls:
                coerced = await self._coerce_text_tool_calls(
                    step_result=step_result,
                    allowed_tool_names=allowed_tool_names,
                    step_index=step_index,
                    event_callback=event_callback,
                )
                if coerced is not None:
                    all_coerced_calls = tuple(tc for tc, _ in coerced)
                    all_coerced_results = tuple(tr for _, tr in coerced)
                    for tc, tr in coerced:
                        if tc.name not in tools_used:
                            tools_used.append(tc.name)
                        # Feed tool results back into messages so the
                        # loop can continue if no terminal tool was hit.
                        messages.append(
                            make_tool_message(
                                tr.output,
                                tool_call_id=tc.id,
                                name=tc.name,
                            )
                        )
                        rules_solver.update_state(tc.name, tr.output)
                    step_ctx.progression = StepProgression.TOOLS_COMPLETED
                    step_traces.append(
                        _build_step_trace(
                            step_ctx,
                            step_result,
                            request_messages,
                            allowed_tool_names,
                            force_tool_call,
                            tool_calls=all_coerced_calls,
                            tool_results=all_coerced_results,
                        )
                    )
                    if event_callback is not None:
                        await event_callback(
                            build_timing_event(step_index, _compute_timing(step_ctx))
                        )
                    # Check if any coerced call was terminal (send_message).
                    terminal_hit = any(tr.is_terminal for _, tr in coerced)
                    if terminal_hit:
                        response = next(
                            (tr.output for _, tr in coerced if tr.is_terminal),
                            response,
                        )
                        stop_reason = StopReason.TERMINAL_TOOL
                        break
                    # Non-terminal coerced calls — continue the loop
                    # so the model can proceed.
                    continue

                response = step_result.assistant_text or response
                if (
                    event_callback is not None
                    and step_result.assistant_text
                    and not streamed_assistant_text
                ):
                    await event_callback(build_chunk_event(step_result.assistant_text))
                step_traces.append(
                    _build_step_trace(
                        step_ctx,
                        step_result,
                        request_messages,
                        allowed_tool_names,
                        force_tool_call,
                    )
                )
                if event_callback is not None:
                    await event_callback(build_timing_event(step_index, _compute_timing(step_ctx)))
                # Detect completely empty response (no text, no tool calls)
                # when tool use was forced — the model failed to comply.
                stop_reason = _resolve_empty_forced_tool_stop_reason(
                    step_index=step_index,
                    force_tool_call=force_tool_call,
                    step_result=step_result,
                )
                if stop_reason is None:
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
                    # If the blocked tool is neither terminal nor an
                    # init-only tool, defer it for execution after the
                    # turn completes instead of permanently discarding
                    # the model's intent.  Init tools are excluded
                    # because deferring them would bypass sequencing.
                    init_tool_names = {
                        rule.tool_name
                        for rule in self._tool_rules
                        if isinstance(rule, InitToolRule)
                    }
                    is_deferrable = (
                        not rules_solver.is_terminal(tool_call.name)
                        and tool_call.name not in init_tool_names
                        and tool_call.name in self._tool_registry
                    )
                    if is_deferrable:
                        deferred_tool_calls.append(tool_call)
                        logger.info(
                            "Step %d: deferring blocked tool call %r for "
                            "post-turn execution (violation: %s)",
                            step_index,
                            tool_call.name,
                            violation,
                        )

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
                        await event_callback(build_tool_return_event(step_index, tool_result))
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
                        await event_callback(build_tool_return_event(step_index, tool_result))
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
                if tool_result.inner_thinking and event_callback is not None:
                    await event_callback(
                        build_thought_event(step_index, tool_result.inner_thinking)
                    )
                rules_solver.update_state(tool_call.name, tool_result.output)
                # Track consecutive failures for exclusion.
                if tool_result.is_error:
                    _prev_failed_tool = last_failed_tool
                    last_failed_tool = tool_call.name
                else:
                    _prev_failed_tool = None
                    last_failed_tool = None
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
                    step_ctx,
                    step_result,
                    request_messages,
                    allowed_tool_names,
                    force_tool_call,
                    tool_results=tuple(tool_results),
                )
            )
            if event_callback is not None:
                await event_callback(build_timing_event(step_index, _compute_timing(step_ctx)))

            if rule_violation_hit:
                messages.append(
                    _sandwich_message(
                        "Tool rule violation — the tool was not allowed at "
                        "this point. Check allowed tools and try again."
                    )
                )
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
                        if (
                            messages
                            and hasattr(messages[0], "type")
                            and getattr(messages[0], "type", "") == "system"
                        ):
                            from anima_server.services.agent.messages import make_system_message

                            messages[0] = make_system_message(system_prompt)
                except Exception:
                    logger.debug("Memory refresh between steps failed", exc_info=True)

            if terminal_tool_hit or awaiting_approval:
                break

            # Continue the loop only if a heartbeat was requested, a
            # tool error occurred (give the model a chance to recover),
            # or a rule violation was hit (already handled above).
            any_heartbeat = any(tr.heartbeat_requested for tr in tool_results)
            any_error = any(tr.is_error for tr in tool_results)
            if any_heartbeat or any_error:
                # Sandwich message: inject a system-as-user message
                # explaining WHY the loop continues, so the model
                # has context for the next step.
                if any_error:
                    failed_names = [tr.name for tr in tool_results if tr.is_error]
                    sandwich = _sandwich_message(
                        f"Tool call failed ({', '.join(failed_names)}). "
                        "You may retry with corrected arguments or "
                        "respond to the user."
                    )
                else:
                    sandwich = _sandwich_message(
                        "Heartbeat received. Continue with your next "
                        "tool call or send_message when ready."
                    )
                messages.append(sandwich)
                continue

            # No heartbeat and no error — the model is done with
            # non-terminal tools.  Fall through to end the turn.
            # The response will be empty, triggering the default.
            break
        else:
            stop_reason = StopReason.MAX_STEPS

        # --- Execute deferred tool calls ---
        # Tool calls that were blocked by rule violations (e.g. InitToolRule)
        # but are valid non-terminal, non-init tools get executed now so the
        # model's intent is preserved.
        if deferred_tool_calls:
            if stop_reason != StopReason.TERMINAL_TOOL:
                logger.warning(
                    "Turn ended with stop reason %r before %d deferred tool call(s) could run: %s",
                    stop_reason.value,
                    len(deferred_tool_calls),
                    ", ".join(tc.name for tc in deferred_tool_calls),
                )
            else:
                deferred_step_index = len(step_traces)
                for dtc in deferred_tool_calls:
                    if dtc.name in tools_used:
                        logger.info(
                            "Skipping deferred tool call %r — already "
                            "executed during the turn (call_id=%s)",
                            dtc.name,
                            dtc.id,
                        )
                        continue
                    try:
                        deferred_result = await self._tool_executor.execute(
                            dtc,
                            is_terminal=False,
                        )
                        if dtc.name not in tools_used:
                            tools_used.append(dtc.name)
                        logger.info(
                            "Deferred tool call %r executed successfully (call_id=%s, error=%s)",
                            dtc.name,
                            dtc.id,
                            deferred_result.is_error,
                        )
                        if event_callback is not None:
                            await event_callback(build_tool_call_event(deferred_step_index, dtc))
                            await event_callback(
                                build_tool_return_event(
                                    deferred_step_index,
                                    deferred_result,
                                )
                            )
                            if deferred_result.inner_thinking:
                                await event_callback(
                                    build_thought_event(
                                        deferred_step_index,
                                        deferred_result.inner_thinking,
                                    )
                                )
                    except Exception:
                        logger.warning(
                            "Deferred tool call %r failed",
                            dtc.name,
                            exc_info=True,
                        )

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
            if tool_result.inner_thinking:
                await event_callback(build_thought_event(0, tool_result.inner_thinking))

        step_ctx.progression = StepProgression.TOOLS_COMPLETED
        request_messages = _snapshot_messages(messages)
        allowed_tool_names = tuple(sorted(rules_solver.get_allowed_tools(self._tool_names)))
        step_traces.append(
            _build_step_trace(
                step_ctx,
                StepExecutionResult(),  # no LLM result for this step
                request_messages,
                allowed_tool_names,
                False,
                llm_invoked=False,
                tool_calls=(tool_call,),
                tool_results=(tool_result,),
            )
        )
        if event_callback is not None:
            await event_callback(build_timing_event(0, _compute_timing(step_ctx)))

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

        allowed_tool_names = tuple(sorted(rules_solver.get_allowed_tools(self._tool_names)))
        force_tool_call = bool(allowed_tool_names) and (
            rules_solver.should_force_tool_call() or "send_message" in allowed_tool_names
        )
        if event_callback is not None:
            await event_callback(
                build_step_request_event(
                    1,
                    request_messages=_snapshot_messages(messages),
                    allowed_tools=allowed_tool_names,
                    force_tool_call=force_tool_call,
                )
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

        if event_callback is not None:
            await event_callback(
                build_step_result_event(
                    1,
                    step_result=step_result,
                )
            )
            if not step_result.assistant_text and not step_result.tool_calls:
                await event_callback(
                    build_warning_event(
                        1,
                        code="empty_step_result",
                        message="LLM returned no assistant text and no tool calls for this step.",
                    )
                )
            if step_result.reasoning_content:
                await event_callback(
                    build_reasoning_event(
                        1,
                        step_result.reasoning_content,
                        step_result.reasoning_signature,
                    )
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
                follow_ctx,
                step_result,
                follow_request_messages,
                allowed_tool_names,
                force_tool_call,
            )
        )
        if event_callback is not None:
            await event_callback(build_timing_event(1, _compute_timing(follow_ctx)))

        stop_reason = _resolve_empty_forced_tool_stop_reason(
            step_index=1,
            force_tool_call=force_tool_call,
            step_result=step_result,
        )
        if stop_reason is None:
            stop_reason = StopReason.END_TURN

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
        extra_tool_schemas: Sequence[dict[str, Any]] = (),
    ) -> tuple[StepExecutionResult, bool, StepContext]:
        ctx = StepContext(
            step_index=step_index,
            progression=StepProgression.START,
            start_time=time.monotonic(),
        )
        # Server-side tools (LangChain tool objects)
        server_tools: list[Any] = [
            self._tool_registry[name]
            for name in allowed_tool_names
            if name in self._tool_registry
        ]
        # Client-side action tools (OpenAI function-call dicts) — only
        # include those that are in the allowed set for this step.
        action_tools: list[dict[str, Any]] = [
            s for s in extra_tool_schemas
            if s.get("function", {}).get("name") in allowed_tool_names
        ]
        request = LLMRequest(
            messages=tuple(messages),
            user_id=user_id,
            conversation_turn_count=conversation_turn_count,
            step_index=step_index,
            max_steps=self._max_steps,
            system_prompt=system_prompt,
            available_tools=tuple(server_tools) + tuple(action_tools),
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
                        self._adapter.invoke(request),
                        timeout=timeout,
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
                        raise RuntimeError("Adapter stream ended without a final step result.")
                    if step_result.ttft_ms is not None:
                        ttft_time = ctx.start_time + (step_result.ttft_ms / 1000)
                        if ctx.ttft_time is None or ttft_time < ctx.ttft_time:
                            ctx.ttft_time = ttft_time

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
                    attempt,
                    retry_limit + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        # Should never reach here, but satisfy the type checker.
        assert last_exc is not None
        raise last_exc

    async def _coerce_text_tool_calls(
        self,
        *,
        step_result: StepExecutionResult,
        allowed_tool_names: Sequence[str],
        step_index: int,
        event_callback: StreamEventCallback | None,
    ) -> list[tuple[ToolCall, ToolExecutionResult]] | None:
        """Detect and execute tool calls that the model output as plain text.

        Some models (especially smaller ones) emit tool calls like
        ``send_message("hello")`` or ``note_to_self("observation")`` as
        plain text instead of structured tool calls.  This method parses
        those patterns and executes them as if they were real tool calls,
        keeping the cognitive loop intact.
        """
        if not step_result.assistant_text.strip():
            return None

        parsed = _parse_text_tool_calls(
            step_result.assistant_text,
            set(self._tool_registry.keys()),
        )
        if not parsed:
            # No recognizable tool calls in the text.  Fall back to
            # coercing the entire text as a send_message if available.
            if "send_message" not in self._tool_registry:
                return None
            parsed = [
                _ParsedTextToolCall(
                    name="send_message",
                    arguments={"message": step_result.assistant_text.strip()},
                )
            ]

        results: list[tuple[ToolCall, ToolExecutionResult]] = []
        for i, ptc in enumerate(parsed):
            if ptc.name not in self._tool_registry:
                continue
            tool_call = ToolCall(
                id=f"synthetic-{ptc.name}-{step_index}-{i}",
                name=ptc.name,
                arguments=ptc.arguments,
            )
            # send_message is the only terminal tool in the cognitive loop.
            is_terminal = ptc.name == "send_message"
            tool_result = await self._tool_executor.execute(
                tool_call,
                is_terminal=is_terminal,
            )
            if event_callback is not None:
                await event_callback(build_tool_call_event(step_index, tool_call))
                await event_callback(build_tool_return_event(step_index, tool_result))
                if tool_result.inner_thinking:
                    await event_callback(
                        build_thought_event(step_index, tool_result.inner_thinking)
                    )
            results.append((tool_call, tool_result))

        return results if results else None


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


# Matches: tool_name("content") or tool_name('content') with optional triple-quotes.
# Captures: group 'name' = tool name, groups 1-4 = content variants.
_TEXT_TOOL_CALL_RE = re.compile(
    r"^(?P<name>[a-z_][a-z0-9_]*)\(\s*(?:"
    r'"((?:[^"\\]|\\.)*)"|'  # double-quoted content
    r"'((?:[^'\\]|\\.)*)'|"  # single-quoted content
    r'"""(.*?)"""|'  # triple-double-quoted
    r"'''(.*?)'''"  # triple-single-quoted
    r")\s*\)$",
    re.DOTALL,
)

# Matches: tool_name {"key": "..."} or tool_name({"key": "..."})
_TEXT_TOOL_CALL_JSON_RE = re.compile(
    r"^(?P<name>[a-z_][a-z0-9_]*)\s*\(?\s*(?P<json>\{.*?\})\s*\)?$",
    re.DOTALL,
)

# Matches Letta/MemGPT-style opening tags: <function=tool_name>
_TEXT_TOOL_CALL_FUNCTION_TAG_RE = re.compile(
    r"<function=(?P<name>[a-z_][a-z0-9_]*)>",
)

# Matches Letta/MemGPT-style parameter tags: <parameter=name>value</parameter>
_PARAMETER_TAG_RE = re.compile(
    r"<parameter=(\w+)>\s*([\s\S]*?)\s*</parameter>",
)


@dataclass(frozen=True, slots=True)
class _ParsedTextToolCall:
    """A tool call parsed from plain text output."""

    name: str
    arguments: dict[str, object]


def _parse_text_tool_calls(
    text: str,
    known_tool_names: set[str],
) -> list[_ParsedTextToolCall]:
    """Parse tool calls that models output as plain text instead of structured calls.

    Recognizes patterns like:
    - ``tool_name("string argument")``
    - ``tool_name {"key": "value"}``
    - ``tool_name({"key": "value"})``
    - ``<function=tool_name>content</function>``  (Letta/MemGPT style)

    Only matches tool names that exist in ``known_tool_names``.
    Returns a list of parsed calls (may contain multiple if text has
    consecutive calls separated by newlines).
    """
    results: list[_ParsedTextToolCall] = []
    stripped = text.strip()

    # Try single-call patterns first (most common case).
    parsed = _try_parse_single_text_tool_call(stripped, known_tool_names)
    if parsed is not None:
        results.append(parsed)
        return results

    # Try line-by-line for consecutive tool calls.
    for line in stripped.split("\n"):
        line = line.strip()
        if not line:
            continue
        parsed = _try_parse_single_text_tool_call(line, known_tool_names)
        if parsed is not None:
            results.append(parsed)

    if results:
        return results

    # Try Letta/MemGPT-style <function=tool_name>content</function> tags.
    # This handles multi-line content blocks and multiple tags in one response.
    results = _parse_function_tag_tool_calls(stripped, known_tool_names)

    return results


def _parse_function_tag_tool_calls(
    text: str,
    known_tool_names: set[str],
) -> list[_ParsedTextToolCall]:
    """Parse Letta/MemGPT-style ``<function=name>content</function>`` blocks.

    Handles multiple blocks in a single response and content that spans
    multiple lines.  The closing ``</function>`` tag may be absent (the
    regex consumes to end-of-string in that case).  Content is mapped to
    the tool's first argument via ``_infer_first_arg_name``.
    """
    results: list[_ParsedTextToolCall] = []
    matches = list(_TEXT_TOOL_CALL_FUNCTION_TAG_RE.finditer(text))
    for index, match in enumerate(matches):
        name = match.group("name")
        if name not in known_tool_names:
            continue

        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw_content = text[match.end() : next_start]
        closing_index = raw_content.find("</function>")
        if closing_index != -1:
            raw_content = raw_content[:closing_index]

        content = raw_content.strip()
        if not content:
            continue
        # Try to parse the content as JSON first (model may emit a JSON
        # object inside the function tags).
        try:
            data = json.loads(content)
            if isinstance(data, dict) and data:
                results.append(_ParsedTextToolCall(name=name, arguments=data))
                continue
        except (json.JSONDecodeError, ValueError):
            pass

        # Try parsing Letta/MemGPT-style <parameter=name>value</parameter> tags.
        param_matches = _PARAMETER_TAG_RE.findall(content)
        if param_matches:
            results.append(
                _ParsedTextToolCall(
                    name=name,
                    arguments={k: v.strip() for k, v in param_matches},
                )
            )
            continue

        arg_name = _infer_first_arg_name(name)
        results.append(_ParsedTextToolCall(name=name, arguments={arg_name: content}))
    return results


def _try_parse_single_text_tool_call(
    text: str,
    known_tool_names: set[str],
) -> _ParsedTextToolCall | None:
    """Try to parse a single text tool call."""
    # Pattern 1: tool_name("string")
    match = _TEXT_TOOL_CALL_RE.match(text)
    if match:
        name = match.group("name")
        if name not in known_tool_names:
            return None
        content = next(g for g in match.groups()[1:] if g is not None)
        content = content.replace('\\"', '"').replace("\\'", "'")
        # Infer the argument name from the tool's first parameter.
        arg_name = _infer_first_arg_name(name)
        return _ParsedTextToolCall(name=name, arguments={arg_name: content})

    # Pattern 2: tool_name {"key": "value"} or tool_name({"key": "value"})
    json_match = _TEXT_TOOL_CALL_JSON_RE.match(text)
    if json_match:
        name = json_match.group("name")
        if name not in known_tool_names:
            return None
        try:
            data = json.loads(json_match.group("json"))
            if isinstance(data, dict):
                return _ParsedTextToolCall(name=name, arguments=data)
        except json.JSONDecodeError:
            pass

    return None


# Map of known tool name -> first argument name for single-string-arg coercion.
_FIRST_ARG_NAMES: dict[str, str] = {
    "send_message": "message",
    "note_to_self": "note",
    "core_memory_append": "content",
    "save_to_memory": "content",
}


def _infer_first_arg_name(tool_name: str) -> str:
    return _FIRST_ARG_NAMES.get(tool_name, "input")


_SANDWICH_PREFIX = "[This is an automated system message hidden from the user] "


def _sandwich_message(reason: str) -> object:
    """Create an inter-step system-as-user message explaining why the
    agent loop is continuing.

    These "sandwich" messages give the model context between steps
    (e.g. "function failed", "heartbeat received") so it can adjust
    its next action.  Formatted as user-role messages with a prefix
    that tells the model they are hidden from the user.
    """
    from anima_server.services.agent.messages import make_user_message

    return make_user_message(f"{_SANDWICH_PREFIX}{reason}")


def _default_response(stop_reason: StopReason) -> str:
    if stop_reason == StopReason.MAX_STEPS:
        return "Agent runtime reached the maximum step limit without a final response."
    if stop_reason == StopReason.AWAITING_APPROVAL:
        return "Agent runtime is waiting for approval before running a tool."
    if stop_reason == StopReason.EMPTY_RESPONSE:
        return (
            "I'm sorry, I wasn't able to generate a response. "
            "Could you try rephrasing or sending your message again?"
        )
    if stop_reason == StopReason.CANCELLED:
        return ""
    return ""


def _resolve_empty_forced_tool_stop_reason(
    *,
    step_index: int,
    force_tool_call: bool,
    step_result: StepExecutionResult,
) -> StopReason | None:
    if not force_tool_call or step_result.assistant_text.strip() or step_result.tool_calls:
        return None
    logger.warning(
        "Step %d: force_tool_call was set but LLM produced "
        "no text and no tool calls; marking as empty_response",
        step_index,
    )
    return StopReason.EMPTY_RESPONSE


def _compute_timing(ctx: StepContext) -> StepTiming:
    now = time.monotonic()
    return StepTiming(
        step_duration_ms=round((now - ctx.start_time) * 1000, 2) if ctx.start_time else None,
        llm_duration_ms=round((ctx.llm_end_time - ctx.start_time) * 1000, 2)
        if ctx.llm_end_time and ctx.start_time
        else None,
        ttft_ms=round((ctx.ttft_time - ctx.start_time) * 1000, 2)
        if ctx.ttft_time and ctx.start_time
        else None,
    )


def _build_step_trace(
    ctx: StepContext,
    step_result: StepExecutionResult,
    request_messages: tuple[MessageSnapshot, ...],
    allowed_tool_names: tuple[str, ...],
    force_tool_call: bool,
    *,
    llm_invoked: bool = True,
    tool_calls: tuple[ToolCall, ...] | None = None,
    tool_results: tuple[ToolExecutionResult, ...] = (),
) -> StepTrace:
    return StepTrace(
        step_index=ctx.step_index,
        llm_invoked=llm_invoked,
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


def _tool_schema(tool: Any) -> dict[str, Any]:
    """Extract a JSON-safe schema dict from a tool for dry-run output."""
    schema: dict[str, Any] = {"name": _tool_name(tool)}
    if hasattr(tool, "description"):
        schema["description"] = tool.description
    if hasattr(tool, "args_schema"):
        try:
            schema["parameters"] = tool.args_schema.model_json_schema()
        except Exception:
            logger.warning("Failed to extract schema for tool %s", _tool_name(tool))
    return schema


_INJECTED_SCHEMA_KEYS = {"thinking", "request_heartbeat"}


def _strip_thinking_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove injected parameters (``thinking``, ``request_heartbeat``)
    from a tool schema so they don't leak in client-facing dry-run output."""
    params = schema.get("parameters")
    if not isinstance(params, dict):
        return schema
    props = params.get("properties")
    if isinstance(props, dict) and _INJECTED_SCHEMA_KEYS & set(props):
        schema = dict(schema)
        schema["parameters"] = dict(params)
        schema["parameters"]["properties"] = {
            k: v for k, v in props.items() if k not in _INJECTED_SCHEMA_KEYS
        }
        required = params.get("required", [])
        if isinstance(required, list):
            filtered = [r for r in required if r not in _INJECTED_SCHEMA_KEYS]
            if len(filtered) != len(required):
                schema["parameters"]["required"] = filtered
    return schema


def _tool_name(tool: Any) -> str:
    return getattr(tool, "name", "") or getattr(tool, "__name__", "")
