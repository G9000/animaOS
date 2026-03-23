from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from threading import Lock

logger = logging.getLogger(__name__)

import contextlib

from sqlalchemy.orm import Session, sessionmaker

from anima_server.config import settings
from anima_server.models import AgentMessage, AgentRun, AgentThread
from anima_server.services.agent.compaction import (
    CompactionResult,
    compact_thread_context,
)
from anima_server.services.agent.companion import (
    AnimaCompanion,
    get_companion,
    get_or_build_companion,
    invalidate_companion,
)
from anima_server.services.agent.consolidation import schedule_background_memory_consolidation
from anima_server.services.agent.llm import ContextWindowOverflowError, invalidate_llm_cache
from anima_server.services.agent.memory_blocks import MemoryBlock, build_runtime_memory_blocks
from anima_server.services.agent.persistence import (
    append_user_message,
    cancel_run,
    clear_approval_checkpoint,
    count_messages_by_role,
    create_run,
    get_or_create_thread,
    list_transcript_messages,
    load_approval_checkpoint,
    mark_run_failed,
    persist_agent_result,
    reset_thread,
    save_approval_checkpoint,
)
from anima_server.services.agent.reflection import schedule_reflection
from anima_server.services.agent.runtime import AgentRuntime, build_loop_runtime
from anima_server.services.agent.runtime_types import (
    DryRunResult,
    StepFailedError,
    StopReason,
    ToolCall,
)
from anima_server.services.agent.sequencing import (
    count_persisted_result_messages,
    reserve_message_sequences,
)
from anima_server.services.agent.state import AgentResult, StoredMessage
from anima_server.services.agent.streaming import (
    AgentStreamEvent,
    build_approval_pending_event,
    build_cancelled_event,
    build_done_event,
    build_error_event,
    build_usage_event,
    summarize_usage,
)
from anima_server.services.agent.system_prompt import invalidate_system_prompt_template_cache
from anima_server.services.agent.tool_context import (
    ToolContext,
    clear_tool_context,
    set_tool_context,
)
from anima_server.services.agent.turn_coordinator import get_user_lock
from anima_server.services.data_crypto import df

_runner_lock = Lock()
_cached_runner: AgentRuntime | None = None


def get_or_build_runner() -> AgentRuntime:
    global _cached_runner
    if _cached_runner is not None:
        return _cached_runner

    with _runner_lock:
        if _cached_runner is None:
            _cached_runner = build_loop_runtime()
        return _cached_runner


def _get_companion(user_id: int) -> AnimaCompanion:
    """Return the AnimaCompanion singleton for *user_id*."""
    runtime = get_or_build_runner()
    return get_or_build_companion(runtime, user_id)


def ensure_agent_ready() -> None:
    runner = get_or_build_runner()
    runner.prepare_system_prompt()


def invalidate_agent_runtime_cache() -> None:
    global _cached_runner
    with _runner_lock:
        _cached_runner = None
    invalidate_companion()
    invalidate_llm_cache()
    invalidate_system_prompt_template_cache()


async def run_agent(user_message: str, user_id: int, db: Session, *, source: str | None = None) -> AgentResult:
    return await _execute_agent_turn(user_message, user_id, db, source=source)


async def cancel_agent_run(run_id: int, user_id: int, db: Session) -> AgentRun | None:
    """Cancel a running agent turn by run id."""
    run = cancel_run(db, run_id)
    if run is None:
        return None
    companion = get_companion(user_id)
    if companion is not None:
        companion.set_cancel(run_id)
    db.commit()
    return run


async def dry_run_agent(user_message: str, user_id: int, db: Session) -> DryRunResult:
    """Execute a dry run: build the full prompt but do not call the LLM.

    Does not create any DB records (threads, messages, runs).
    """
    companion = _get_companion(user_id)

    # Look up existing thread without creating one.
    from sqlalchemy import select as sa_select

    from anima_server.models import AgentThread as AgentThreadModel

    thread = db.scalar(sa_select(AgentThreadModel).where(AgentThreadModel.user_id == user_id))

    history: list[StoredMessage] = []
    memory_blocks: tuple[MemoryBlock, ...] = ()
    if thread is not None:
        companion.thread_id = thread.id
        history = companion.ensure_history_loaded(db)
        memory_blocks = companion.ensure_memory_loaded(db)

    runner = get_or_build_runner()
    result = await runner.invoke(
        user_message,
        user_id,
        history,
        memory_blocks=memory_blocks,
        dry_run=True,
    )
    assert isinstance(result, DryRunResult)
    return result


async def approve_or_deny_turn(
    run_id: int,
    user_id: int,
    approved: bool,
    db: Session,
    *,
    denial_reason: str | None = None,
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None = None,
) -> AgentResult:
    """Resume a turn after an approval decision.

    On approve: execute the pending tool directly, then optionally one LLM
    follow-up.  On deny: inject denial as a tool error and make one LLM
    follow-up so the companion can respond.
    """
    checkpoint = load_approval_checkpoint(db, run_id)
    if checkpoint is None:
        raise ValueError(f"Run {run_id} is not awaiting approval")

    run, approval_msg = checkpoint
    if run.user_id != user_id:
        raise PermissionError("Not authorized for this run")

    # Reconstruct the ToolCall from the persisted approval message.
    tool_call = ToolCall(
        id=approval_msg.tool_call_id or "tool-call-0",
        name=approval_msg.tool_name or "",
        arguments=approval_msg.tool_args_json
        if isinstance(approval_msg.tool_args_json, dict)
        else {},
    )

    # Resolve the checkpoint now — the re-entry takes over.
    clear_approval_checkpoint(db, run, approval_msg)
    db.flush()

    companion = _get_companion(user_id)
    thread = db.get(AgentThread, run.thread_id)
    if thread is None:
        raise ValueError("Thread not found")
    companion.thread_id = thread.id

    history = companion.ensure_history_loaded(db)
    memory_blocks = companion.ensure_memory_loaded(db)
    conversation_turn_count = count_messages_by_role(db, thread.id, "user")

    cancel_event = companion.create_cancel_event(run.id)
    set_tool_context(ToolContext(db=db, user_id=user_id, thread_id=thread.id))
    try:
        runner = get_or_build_runner()
        result = await runner.resume_after_approval(
            approved=approved,
            tool_call=tool_call,
            user_id=user_id,
            history=history,
            denial_reason=denial_reason,
            memory_blocks=memory_blocks,
            conversation_turn_count=conversation_turn_count,
            event_callback=event_callback,
            cancel_event=cancel_event,
        )
    except StepFailedError as exc:
        mark_run_failed(db, run, str(exc.cause))
        db.commit()
        raise exc.cause from exc
    except Exception as exc:
        mark_run_failed(db, run, str(exc))
        db.commit()
        raise
    finally:
        companion.clear_cancel_event(run.id)
        clear_tool_context()

    # Handle cancellation during resume
    if result.stop_reason == StopReason.CANCELLED.value:
        cancel_run(db, run.id)
        db.commit()
        if event_callback is not None:
            await event_callback(build_cancelled_event(run.id))
        return result

    # Persist result
    result_message_count = count_persisted_result_messages(result)
    persist_agent_result(
        db,
        thread=thread,
        run=run,
        result=result,
        initial_sequence_id=(
            reserve_message_sequences(
                db,
                thread_id=thread.id,
                count=result_message_count,
            )
            if result_message_count > 0
            else None
        ),
    )
    compact_thread_context(
        db,
        thread=thread,
        run_id=run.id,
        trigger_token_limit=max(
            1,
            int(settings.agent_max_tokens * settings.agent_compaction_trigger_ratio),
        ),
        keep_last_messages=max(1, settings.agent_compaction_keep_last_messages),
        reserved_prompt_tokens=(
            result.prompt_budget.system_prompt_token_estimate
            if result.prompt_budget is not None
            else 0
        ),
    )
    db.commit()
    _refresh_companion_history(user_id=user_id, db=db)

    # Post-turn hooks
    _run_post_turn_hooks(
        user_id=user_id,
        thread_id=thread.id,
        user_message="",  # no new user message on resume
        result=result,
        db_factory=_build_db_factory(db),
    )

    if event_callback is not None:
        usage = summarize_usage(result)
        if usage is not None:
            await event_callback(build_usage_event(usage))
        await event_callback(build_done_event(result))
    return result


async def stream_approve_or_deny(
    run_id: int,
    user_id: int,
    approved: bool,
    db: Session,
    *,
    denial_reason: str | None = None,
) -> AsyncGenerator[AgentStreamEvent, None]:
    """Streaming wrapper for approve_or_deny_turn."""
    queue: asyncio.Queue[AgentStreamEvent | None] = asyncio.Queue(
        maxsize=settings.agent_stream_queue_max_size,
    )

    async def emit(event: AgentStreamEvent) -> None:
        await queue.put(event)

    async def worker() -> None:
        try:
            await approve_or_deny_turn(
                run_id,
                user_id,
                approved,
                db,
                denial_reason=denial_reason,
                event_callback=emit,
            )
        except Exception as exc:
            await queue.put(build_error_event(str(exc)))
        finally:
            await queue.put(None)

    worker_task = asyncio.create_task(worker())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            await asyncio.sleep(0)
            yield event
    except (asyncio.CancelledError, GeneratorExit):
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
        raise
    finally:
        if not worker_task.done():
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task


async def _execute_agent_turn(
    user_message: str,
    user_id: int,
    db: Session,
    *,
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None = None,
    source: str | None = None,
) -> AgentResult:
    user_lock = get_user_lock(user_id)
    async with user_lock:
        return await _execute_agent_turn_locked(
            user_message,
            user_id,
            db,
            event_callback=event_callback,
            source=source,
        )


async def _execute_agent_turn_locked(
    user_message: str,
    user_id: int,
    db: Session,
    *,
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None = None,
    source: str | None = None,
) -> AgentResult:
    # Stage 1: Prepare turn context
    thread, run, user_msg, initial_sequence_id, turn_ctx = await _prepare_turn_context(
        user_message,
        user_id,
        db,
        event_callback=event_callback,
        source=source,
    )

    # Stage 1b: Proactive context management — compact before the LLM call
    # if estimated context usage already exceeds the threshold.
    turn_ctx = await _proactive_compact_if_needed(
        db,
        thread=thread,
        run=run,
        turn_ctx=turn_ctx,
        user_id=user_id,
    )

    # Stage 2: Invoke the runtime
    companion = _get_companion(user_id)
    cancel_event = companion.create_cancel_event(run.id)
    try:
        result = await _invoke_turn_runtime(
            user_message,
            user_id,
            db,
            thread=thread,
            run=run,
            user_msg=user_msg,
            turn_ctx=turn_ctx,
            event_callback=event_callback,
            cancel_event=cancel_event,
        )
    finally:
        companion.clear_cancel_event(run.id)

    # Handle cancellation: persist cancel status and emit event
    if result.stop_reason == StopReason.CANCELLED.value:
        cancel_run(db, run.id)
        db.commit()
        if event_callback is not None:
            await event_callback(build_cancelled_event(run.id))
        return result

    # Handle approval: persist checkpoint and emit event
    if result.stop_reason == StopReason.AWAITING_APPROVAL.value:
        pending_tc = _persist_approval_checkpoint(
            db,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=initial_sequence_id,
        )
        _refresh_companion_history(user_id=user_id, db=db)
        if event_callback is not None:
            if pending_tc is not None:
                await event_callback(
                    build_approval_pending_event(
                        run_id=run.id,
                        tool_name=pending_tc.name,
                        tool_call_id=pending_tc.id,
                        tool_arguments={
                            k: v for k, v in pending_tc.arguments.items() if k != "thinking"
                        }
                        if isinstance(pending_tc.arguments, dict)
                        else {},
                    )
                )
            usage = summarize_usage(result)
            if usage is not None:
                await event_callback(build_usage_event(usage))
            await event_callback(build_done_event(result))
        return result

    # Stage 3: Persist result
    await _persist_turn_result(
        db,
        thread=thread,
        run=run,
        result=result,
        initial_sequence_id=initial_sequence_id,
    )
    _refresh_companion_history(user_id=user_id, db=db)

    # Stage 4: Post-turn hooks
    _run_post_turn_hooks(
        user_id=user_id,
        thread_id=thread.id,
        user_message=user_message,
        result=result,
        db_factory=_build_db_factory(db),
    )

    if event_callback is not None:
        usage = summarize_usage(result)
        if usage is not None:
            await event_callback(build_usage_event(usage))
        await event_callback(build_done_event(result))
    return result


@dataclass(slots=True)
class _TurnContext:
    history: list[StoredMessage]
    conversation_turn_count: int
    memory_blocks: tuple[MemoryBlock, ...]


async def _prepare_turn_context(
    user_message: str,
    user_id: int,
    db: Session,
    *,
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None = None,
    source: str | None = None,
) -> tuple[AgentThread, AgentRun, AgentMessage, int, _TurnContext]:
    """Stage 1: Load thread, persist user message, build memory context.

    Uses the AnimaCompanion cache for static memory blocks and conversation
    history.  Only semantic retrieval (query-dependent) is executed per-turn.
    """
    companion = _get_companion(user_id)

    thread = get_or_create_thread(db, user_id)
    companion.thread_id = thread.id

    # Use cached conversation history when available, otherwise load from DB.
    history = companion.ensure_history_loaded(db)

    run = create_run(
        db,
        thread_id=thread.id,
        user_id=user_id,
        provider=settings.agent_provider,
        model=settings.agent_model,
        mode="streaming" if event_callback is not None else "blocking",
    )
    initial_sequence_id = reserve_message_sequences(
        db,
        thread_id=thread.id,
        count=1,
    )
    user_msg = append_user_message(
        db,
        thread=thread,
        run_id=run.id,
        content=user_message,
        sequence_id=initial_sequence_id,
        source=source,
    )
    conversation_turn_count = count_messages_by_role(db, thread.id, "user")

    # Semantic retrieval is always per-turn (query-dependent).
    semantic_results: list[tuple[int, str, float]] | None = None
    query_embedding: list[float] | None = None
    try:
        from anima_server.services.agent.embeddings import adaptive_filter, hybrid_search

        search_result = await hybrid_search(
            db,
            user_id=user_id,
            query=user_message,
            limit=15,
            similarity_threshold=0.25,
        )
        query_embedding = search_result.query_embedding
        if search_result.items:
            filtered = adaptive_filter(search_result.items)
            semantic_results = [
                (item.id, df(user_id, item.content, table="memory_items", field="content"), score)
                for item, score in filtered
            ]
    except Exception:
        pass

    # Use companion-cached static blocks, reload from DB only if stale.
    static_blocks = companion.ensure_memory_loaded(db)

    # If we have semantic results or a query embedding, build fresh
    # blocks so query-aware scoring can re-rank facts/preferences/etc.
    if semantic_results or query_embedding is not None:
        memory_blocks = build_runtime_memory_blocks(
            db,
            user_id=user_id,
            thread_id=thread.id,
            semantic_results=semantic_results,
            query_embedding=query_embedding,
            query=user_message,
        )
        # Re-populate the cache with the freshly-built static subset so
        # the next turn that has no semantic changes still benefits.
        companion.set_memory_cache(
            tuple(
                b for b in memory_blocks if b.label not in ("relevant_memories", "knowledge_graph")
            )
        )
    else:
        memory_blocks = static_blocks

    # Feedback signals (best-effort)
    try:
        from anima_server.services.agent.feedback_signals import (
            apply_memory_correction,
            collect_feedback_signals,
            record_feedback_signals,
        )

        signals = collect_feedback_signals(
            db,
            user_id=user_id,
            user_message=user_message,
            thread_id=thread.id,
        )
        if signals:
            record_feedback_signals(db, user_id=user_id, signals=signals)
            # When a correction is detected, fix the underlying memory
            if any(s.signal_type == "correction" for s in signals):
                apply_memory_correction(
                    db,
                    user_id=user_id,
                    user_message=user_message,
                    thread_id=thread.id,
                )
    except Exception:
        pass

    # Memory pressure warning: estimate total context usage and inject
    # a warning block when approaching the context window limit.
    memory_blocks = _inject_memory_pressure_warning(
        memory_blocks,
        history,
        companion,
    )

    turn_ctx = _TurnContext(
        history=history,
        conversation_turn_count=conversation_turn_count,
        memory_blocks=memory_blocks,
    )
    return thread, run, user_msg, initial_sequence_id, turn_ctx


_MEMORY_PRESSURE_WARNING = (
    "[SYSTEM NOTE: Your conversation context is getting full. "
    "Consider using save_to_memory to persist important facts, and "
    "keep your responses concise. Older conversation will be "
    "summarized automatically to free space.]"
)

# Warning fires at 80% of context window; compaction fires at the
# configured trigger ratio (default 80% of max_tokens, applied to
# conversation tokens only).  The warning here covers the FULL
# context (blocks + history).
_MEMORY_PRESSURE_RATIO = 0.80


def _inject_memory_pressure_warning(
    memory_blocks: tuple[MemoryBlock, ...],
    history: list[StoredMessage],
    companion: AnimaCompanion,
) -> tuple[MemoryBlock, ...]:
    """Add a memory pressure warning block when context usage is high.

    Only alerts once per pressure window to avoid spamming the agent.
    Resets when the conversation is compacted (history shrinks).
    """
    # Estimate total tokens: memory block chars + history chars, / 4
    block_chars = sum(len(b.value) for b in memory_blocks)
    history_chars = sum(len(m.content or "") for m in history)
    estimated_tokens = (block_chars + history_chars) // 4

    threshold = int(settings.agent_max_tokens * _MEMORY_PRESSURE_RATIO)

    if estimated_tokens < threshold:
        # Below pressure — reset the alert flag if it was set
        if getattr(companion, "_memory_pressure_alerted", False):
            # type: ignore[attr-defined]
            companion._memory_pressure_alerted = False
        return memory_blocks

    # Already alerted this pressure window — don't repeat
    if getattr(companion, "_memory_pressure_alerted", False):
        return memory_blocks

    companion._memory_pressure_alerted = True  # type: ignore[attr-defined]

    warning_block = MemoryBlock(
        label="memory_pressure_warning",
        value=_MEMORY_PRESSURE_WARNING,
        description="Context window pressure alert",
        read_only=True,
    )
    return (*memory_blocks, warning_block)


async def _invoke_turn_runtime(
    user_message: str,
    user_id: int,
    db: Session,
    *,
    thread: AgentThread,
    run: AgentRun,
    user_msg: AgentMessage,
    turn_ctx: _TurnContext,
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> AgentResult:
    """Stage 2: Set tool context and invoke the agent runtime."""
    set_tool_context(ToolContext(db=db, user_id=user_id, thread_id=thread.id))

    async def _refresh_memory() -> tuple[MemoryBlock, ...] | None:
        """Memory refresher callback for in-context memory editing.

        Called by the runtime between steps when a tool signals
        memory_modified.  Returns fresh blocks if memory changed,
        None otherwise.
        """
        companion = _get_companion(user_id)
        if not companion.memory_stale:
            return None
        return build_runtime_memory_blocks(
            db,
            user_id=user_id,
            thread_id=thread.id,
        )

    try:
        runner = get_or_build_runner()
        try:
            return await runner.invoke(
                user_message,
                user_id,
                turn_ctx.history,
                conversation_turn_count=turn_ctx.conversation_turn_count,
                memory_blocks=turn_ctx.memory_blocks,
                event_callback=event_callback,
                cancel_event=cancel_event,
                memory_refresher=_refresh_memory,
            )
        except StepFailedError as exc:
            if not _should_retry_after_compaction(exc):
                raise
            # Context overflow: compact and retry once.
            compacted = _emergency_compact(db, thread=thread, run=run)
            if not compacted:
                raise
            logger.info(
                "Context overflow detected — compacted %d messages, retrying",
                compacted.compacted_message_count,
            )
            turn_ctx = _rebuild_turn_context_after_compaction(
                db,
                user_id=user_id,
                thread=thread,
                user_message=user_message,
                turn_ctx=turn_ctx,
            )
            return await runner.invoke(
                user_message,
                user_id,
                turn_ctx.history,
                conversation_turn_count=turn_ctx.conversation_turn_count,
                memory_blocks=turn_ctx.memory_blocks,
                event_callback=event_callback,
                cancel_event=cancel_event,
                memory_refresher=_refresh_memory,
            )
    except StepFailedError as exc:
        _handle_step_failure(db, run=run, user_msg=user_msg, err=exc)
        raise exc.cause from exc
    except Exception as exc:
        # Remove orphaned user message from active context so it doesn't
        # replay as valid history on the next turn.
        user_msg.is_in_context = False
        db.add(user_msg)
        mark_run_failed(db, run, str(exc))
        db.commit()
        raise
    finally:
        clear_tool_context()


async def _proactive_compact_if_needed(
    db: Session,
    *,
    thread: AgentThread,
    run: AgentRun,
    turn_ctx: _TurnContext,
    user_id: int,
) -> _TurnContext:
    """Pre-flight check: estimate total context tokens and compact if over limit.

    This prevents sending an oversized prompt to the LLM by compacting
    conversation history *before* the first LLM call.
    """
    block_chars = sum(len(b.value) for b in turn_ctx.memory_blocks)
    history_chars = sum(len(m.content or "") for m in turn_ctx.history)
    estimated_tokens = (block_chars + history_chars) // 4

    threshold = int(settings.agent_max_tokens * settings.agent_compaction_trigger_ratio)
    if estimated_tokens <= threshold:
        return turn_ctx

    logger.info(
        "Proactive compaction: estimated %d tokens > threshold %d",
        estimated_tokens,
        threshold,
    )
    result = compact_thread_context(
        db,
        thread=thread,
        run_id=run.id,
        trigger_token_limit=threshold,
        keep_last_messages=max(1, settings.agent_compaction_keep_last_messages),
        reserved_prompt_tokens=block_chars // 4,
    )
    if result is None:
        return turn_ctx

    db.flush()
    logger.info(
        "Proactive compaction: %d messages compacted (%d -> %d estimated tokens)",
        result.compacted_message_count,
        result.estimated_tokens_before,
        result.estimated_tokens_after,
    )

    return _rebuild_turn_context_after_compaction(
        db,
        user_id=user_id,
        thread=thread,
        user_message="",
        turn_ctx=turn_ctx,
    )


def _should_retry_after_compaction(exc: StepFailedError) -> bool:
    """Return True if the step failure looks like a context overflow."""
    if not settings.agent_context_overflow_retry:
        return False
    return isinstance(exc.cause, ContextWindowOverflowError)


def _emergency_compact(
    db: Session,
    *,
    thread: AgentThread,
    run: AgentRun,
) -> CompactionResult | None:
    """Run compaction mid-turn to recover from context overflow.

    Uses aggressive settings: keep fewer messages and reserve no prompt
    tokens (since the overflow already happened).
    """
    keep_last = max(1, settings.agent_compaction_keep_last_messages // 2)
    result = compact_thread_context(
        db,
        thread=thread,
        run_id=run.id,
        trigger_token_limit=1,  # force compaction
        keep_last_messages=keep_last,
        reserved_prompt_tokens=0,
    )
    if result is not None:
        db.flush()
    return result


def _rebuild_turn_context_after_compaction(
    db: Session,
    *,
    user_id: int,
    thread: AgentThread,
    user_message: str,
    turn_ctx: _TurnContext,
) -> _TurnContext:
    """Reload history and memory after emergency compaction."""
    companion = _get_companion(user_id)
    companion.invalidate_history()
    history = companion.ensure_history_loaded(db)
    conversation_turn_count = count_messages_by_role(db, thread.id, "user")
    return _TurnContext(
        history=history,
        conversation_turn_count=conversation_turn_count,
        memory_blocks=turn_ctx.memory_blocks,
    )


def _refresh_companion_history(*, user_id: int, db: Session) -> None:
    """Reload the cached conversation window from persisted in-context history."""
    companion = get_companion(user_id)
    if companion is None:
        return
    companion.invalidate_history()
    companion.ensure_history_loaded(db)


def _handle_step_failure(
    db: Session,
    *,
    run: AgentRun,
    user_msg: AgentMessage,
    err: StepFailedError,
) -> None:
    """Progression-aware cleanup after a step failure.

    Early stages (before the LLM responded) only need to remove the
    orphaned user message.  Later stages mean an assistant message may
    already be buffered in the runtime, so the run is marked failed
    with extra detail.
    """
    stage = err.progression

    # Remove orphaned user message from active context regardless of
    # how far the step progressed — tool side-effects (if any) are
    # committed atomically with the run-failure record below.
    user_msg.is_in_context = False
    db.add(user_msg)

    detail = f"step {err.context.step_index} failed at {stage.name}: {err.cause}"
    mark_run_failed(db, run, detail)
    db.commit()


def _persist_approval_checkpoint(
    db: Session,
    *,
    thread: AgentThread,
    run: AgentRun,
    result: AgentResult,
    initial_sequence_id: int,
) -> ToolCall | None:
    """Persist the agent result plus a role='approval' checkpoint message.

    Called when the runtime stops with ``AWAITING_APPROVAL``.  Persists
    the step traces (assistant message + tool-error result) and then
    adds the approval checkpoint message referencing the pending tool call.

    Returns the pending ``ToolCall`` so the caller can emit the
    ``approval_pending`` streaming event, or ``None`` if the tool call
    could not be reconstructed (run is marked failed in that case).
    """
    # First persist the normal step traces (assistant msg + tool error).
    result_message_count = count_persisted_result_messages(result)
    persist_agent_result(
        db,
        thread=thread,
        run=run,
        result=result,
        initial_sequence_id=(
            reserve_message_sequences(
                db,
                thread_id=thread.id,
                count=result_message_count,
            )
            if result_message_count > 0
            else None
        ),
    )

    # Find the pending tool call from the last step trace.
    pending_tool_call = None
    for trace in reversed(result.step_traces):
        for tr in trace.tool_results:
            if tr.is_error and "Approval required" in tr.output:
                for tc in trace.tool_calls:
                    if tc.id == tr.call_id and tc.name == tr.name:
                        pending_tool_call = tc
                        break
                # Fallback: match by call_id only (name may differ if aliased)
                if pending_tool_call is None:
                    for tc in trace.tool_calls:
                        if tc.id == tr.call_id:
                            pending_tool_call = tc
                            break
                break
        if pending_tool_call is not None:
            break

    if pending_tool_call is None:
        mark_run_failed(db, run, "Could not reconstruct pending tool call for approval checkpoint")
        db.commit()
        return None

    seq_id = reserve_message_sequences(db, thread_id=thread.id, count=1)
    save_approval_checkpoint(
        db,
        thread=thread,
        run=run,
        tool_call=pending_tool_call,
        step_id=None,
        sequence_id=seq_id,
    )

    db.commit()
    return pending_tool_call


async def _persist_turn_result(
    db: Session,
    *,
    thread: AgentThread,
    run: AgentRun,
    result: AgentResult,
    initial_sequence_id: int,
) -> None:
    """Stage 3: Write result to DB and compact if needed.

    Attempts LLM-powered summarization first for richer summaries,
    falling back to fast text-based compaction on failure.
    """
    result_message_count = count_persisted_result_messages(result)
    persist_agent_result(
        db,
        thread=thread,
        run=run,
        result=result,
        initial_sequence_id=(
            reserve_message_sequences(
                db,
                thread_id=thread.id,
                count=result_message_count,
            )
            if result_message_count > 0
            else None
        ),
    )

    # Commit persistence before compaction to avoid holding the DB lock
    # open during a potentially slow LLM summarization call.
    db.commit()

    compaction_kwargs = dict(
        thread=thread,
        run_id=run.id,
        trigger_token_limit=max(
            1,
            int(settings.agent_max_tokens * settings.agent_compaction_trigger_ratio),
        ),
        keep_last_messages=max(1, settings.agent_compaction_keep_last_messages),
        reserved_prompt_tokens=(
            result.prompt_budget.system_prompt_token_estimate
            if result.prompt_budget is not None
            else 0
        ),
    )

    # Try LLM-powered compaction first (best-effort)
    llm_result = None
    try:
        from anima_server.services.agent.compaction import compact_thread_context_with_llm

        llm_result = await compact_thread_context_with_llm(db, **compaction_kwargs)
    except Exception:
        pass

    # Fall back to fast text-based compaction if LLM didn't trigger
    if llm_result is None:
        compact_thread_context(db, **compaction_kwargs)

    db.commit()


def _extract_inner_thoughts(result: AgentResult) -> str:
    """Extract thinking content from tool call arguments for consolidation.

    With the injected ``thinking`` kwarg, every tool call may contain
    private reasoning in its ``thinking`` argument.  We also check
    ``inner_thinking`` on tool results (populated by the executor after
    unpacking the kwarg).
    """
    thoughts: list[str] = []
    for trace in result.step_traces:
        # Primary path: executor unpacks thinking and stores it on the result.
        for tr in trace.tool_results:
            if tr.inner_thinking:
                thoughts.append(tr.inner_thinking.strip())
        # Safety-net fallback: if thinking was not unpacked (e.g. synthetic
        # tool calls from a future code path), check tool call args.
        if not any(tr.inner_thinking for tr in trace.tool_results):
            for tc in trace.tool_calls:
                thought = tc.arguments.get("thinking", "")
                if isinstance(thought, str) and thought.strip():
                    thoughts.append(thought.strip())
    return "\n".join(thoughts)


def _run_post_turn_hooks(
    *,
    user_id: int,
    thread_id: int,
    user_message: str,
    result: AgentResult,
    db_factory: Callable[[], Session],
) -> None:
    """Stage 4: Schedule background memory and reflection work."""
    # Include inner thoughts in the consolidation input so the extraction
    # pipeline can learn from the agent's own reasoning.
    inner_thoughts = _extract_inner_thoughts(result)
    enriched_response = result.response
    if inner_thoughts:
        enriched_response = (
            f"[Agent's inner reasoning]\n{inner_thoughts}\n\n"
            f"[Agent's response to user]\n{result.response}"
        )

    schedule_background_memory_consolidation(
        user_id=user_id,
        user_message=user_message,
        assistant_response=enriched_response,
        thread_id=thread_id,
        db_factory=db_factory,
    )
    schedule_reflection(
        user_id=user_id,
        thread_id=thread_id,
        db_factory=db_factory,
    )


async def stream_agent(
    user_message: str,
    user_id: int,
    db: Session,
    *,
    source: str | None = None,
) -> AsyncGenerator[AgentStreamEvent, None]:
    queue: asyncio.Queue[AgentStreamEvent | None] = asyncio.Queue(
        maxsize=settings.agent_stream_queue_max_size,
    )

    async def emit(event: AgentStreamEvent) -> None:
        await queue.put(event)

    async def worker() -> None:
        try:
            await _execute_agent_turn(
                user_message,
                user_id,
                db,
                event_callback=emit,
                source=source,
            )
        except Exception as exc:
            await queue.put(build_error_event(str(exc)))
        finally:
            await queue.put(None)

    worker_task = asyncio.create_task(worker())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            await asyncio.sleep(0)
            yield event
    except (asyncio.CancelledError, GeneratorExit):
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
        raise
    finally:
        if not worker_task.done():
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task


def list_agent_history(user_id: int, db: Session, *, limit: int = 50) -> list[AgentMessage]:
    return list_transcript_messages(
        db,
        user_id=user_id,
        limit=limit,
    )


async def reset_agent_thread(user_id: int, db: Session) -> None:
    reset_thread(db, user_id)
    db.commit()
    companion = get_companion(user_id)
    if companion is not None:
        companion.reset()


def _build_db_factory(db: Session) -> Callable[[], Session]:
    bind = db.get_bind()
    resolved_bind = getattr(bind, "engine", bind)
    return sessionmaker(
        bind=resolved_bind,
        autoflush=db.autoflush,
        expire_on_commit=db.expire_on_commit,
        class_=type(db),
    )
