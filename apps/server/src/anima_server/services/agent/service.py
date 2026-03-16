from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from threading import Lock

from anima_server.config import settings
from anima_server.services.agent.compaction import compact_thread_context
from anima_server.services.agent.companion import (
    AnimaCompanion,
    get_companion,
    get_or_build_companion,
    invalidate_companion,
)
from anima_server.services.agent.consolidation import schedule_background_memory_consolidation
from anima_server.services.agent.reflection import schedule_reflection
from anima_server.services.agent.tool_context import ToolContext, clear_tool_context, set_tool_context
from anima_server.services.agent.turn_coordinator import get_user_lock
from anima_server.services.agent.memory_blocks import MemoryBlock, build_runtime_memory_blocks
from anima_server.services.agent.llm import invalidate_llm_cache
from anima_server.services.agent.persistence import (
    append_user_message,
    count_messages_by_role,
    create_run,
    get_or_create_thread,
    list_transcript_messages,
    load_thread_history,
    mark_run_failed,
    persist_agent_result,
    reset_thread,
)
from anima_server.services.agent.runtime import AgentRuntime, build_loop_runtime
from anima_server.services.agent.runtime_types import StepFailedError, StepProgression
from anima_server.services.agent.sequencing import (
    count_persisted_result_messages,
    reserve_message_sequences,
)
from anima_server.services.agent.state import AgentResult, StoredMessage
from anima_server.services.agent.streaming import (
    AgentStreamEvent,
    build_done_event,
    build_error_event,
    build_usage_event,
    summarize_usage,
)
from anima_server.services.agent.system_prompt import invalidate_system_prompt_template_cache
from anima_server.models import AgentMessage, AgentRun, AgentThread
from sqlalchemy.orm import Session, sessionmaker

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


async def run_agent(user_message: str, user_id: int, db: Session) -> AgentResult:
    return await _execute_agent_turn(user_message, user_id, db)


async def _execute_agent_turn(
    user_message: str,
    user_id: int,
    db: Session,
    *,
    event_callback: Callable[[AgentStreamEvent],
                             Awaitable[None]] | None = None,
) -> AgentResult:
    user_lock = get_user_lock(user_id)
    async with user_lock:
        return await _execute_agent_turn_locked(
            user_message, user_id, db, event_callback=event_callback,
        )


async def _execute_agent_turn_locked(
    user_message: str,
    user_id: int,
    db: Session,
    *,
    event_callback: Callable[[AgentStreamEvent],
                             Awaitable[None]] | None = None,
) -> AgentResult:
    # Stage 1: Prepare turn context
    thread, run, user_msg, initial_sequence_id, turn_ctx = await _prepare_turn_context(
        user_message, user_id, db, event_callback=event_callback,
    )

    # Stage 2: Invoke the runtime
    result = await _invoke_turn_runtime(
        user_message, user_id, db,
        thread=thread, run=run, user_msg=user_msg,
        turn_ctx=turn_ctx,
        event_callback=event_callback,
    )

    # Stage 3: Persist result
    _persist_turn_result(
        db, thread=thread, run=run, result=result,
        initial_sequence_id=initial_sequence_id,
    )

    # Stage 4: Post-turn hooks
    _run_post_turn_hooks(
        user_id=user_id, thread_id=thread.id,
        user_message=user_message, result=result,
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
    event_callback: Callable[[AgentStreamEvent],
                             Awaitable[None]] | None = None,
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
    )
    conversation_turn_count = count_messages_by_role(db, thread.id, "user")

    # Semantic retrieval is always per-turn (query-dependent).
    semantic_results: list[tuple[int, str, float]] | None = None
    try:
        from anima_server.services.agent.embeddings import semantic_search
        hits = await semantic_search(
            db, user_id=user_id, query=user_message,
            limit=8, similarity_threshold=0.35,
        )
        if hits:
            semantic_results = [(item.id, item.content, score)
                                for item, score in hits]
    except Exception:  # noqa: BLE001
        pass

    # Use companion-cached static blocks, reload from DB only if stale.
    static_blocks = companion.ensure_memory_loaded(db)

    # If we have semantic results, build a full block set with the
    # semantic block injected.  Otherwise use static blocks as-is.
    if semantic_results:
        memory_blocks = build_runtime_memory_blocks(
            db, user_id=user_id, thread_id=thread.id,
            semantic_results=semantic_results,
        )
        # Re-populate the cache with the freshly-built static subset so
        # the next turn that has no semantic changes still benefits.
        companion.set_memory_cache(tuple(
            b for b in memory_blocks if b.label != "relevant_memories"
        ))
    else:
        memory_blocks = static_blocks

    # Feedback signals (best-effort)
    try:
        from anima_server.services.agent.feedback_signals import (
            collect_feedback_signals, record_feedback_signals,
        )
        signals = collect_feedback_signals(
            db, user_id=user_id, user_message=user_message, thread_id=thread.id,
        )
        if signals:
            record_feedback_signals(db, user_id=user_id, signals=signals)
    except Exception:  # noqa: BLE001
        pass

    # Append the user message to the companion's conversation window.
    companion.append_to_window(
        [StoredMessage(role="user", content=user_message)])

    turn_ctx = _TurnContext(
        history=history,
        conversation_turn_count=conversation_turn_count,
        memory_blocks=memory_blocks,
    )
    return thread, run, user_msg, initial_sequence_id, turn_ctx


async def _invoke_turn_runtime(
    user_message: str,
    user_id: int,
    db: Session,
    *,
    thread: AgentThread,
    run: AgentRun,
    user_msg: AgentMessage,
    turn_ctx: _TurnContext,
    event_callback: Callable[[AgentStreamEvent],
                             Awaitable[None]] | None = None,
) -> AgentResult:
    """Stage 2: Set tool context and invoke the agent runtime."""
    set_tool_context(ToolContext(db=db, user_id=user_id, thread_id=thread.id))
    try:
        runner = get_or_build_runner()
        return await runner.invoke(
            user_message,
            user_id,
            turn_ctx.history,
            conversation_turn_count=turn_ctx.conversation_turn_count,
            memory_blocks=turn_ctx.memory_blocks,
            event_callback=event_callback,
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

    if stage <= StepProgression.LLM_REQUESTED:
        # LLM never responded — only the user message is orphaned.
        user_msg.is_in_context = False
        db.add(user_msg)
    else:
        # A partial response or tool execution was in progress.
        user_msg.is_in_context = False
        db.add(user_msg)

    detail = f"step {err.context.step_index} failed at {stage.name}: {err.cause}"
    mark_run_failed(db, run, detail)
    db.commit()


def _persist_turn_result(
    db: Session,
    *,
    thread: AgentThread,
    run: AgentRun,
    result: AgentResult,
    initial_sequence_id: int,
) -> None:
    """Stage 3: Write result to DB and compact if needed."""
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
            int(settings.agent_max_tokens *
                settings.agent_compaction_trigger_ratio),
        ),
        keep_last_messages=max(
            1, settings.agent_compaction_keep_last_messages),
        reserved_prompt_tokens=(
            result.prompt_budget.system_prompt_token_estimate
            if result.prompt_budget is not None
            else 0
        ),
    )
    db.commit()


def _run_post_turn_hooks(
    *,
    user_id: int,
    thread_id: int,
    user_message: str,
    result: AgentResult,
    db_factory: Callable[[], Session],
) -> None:
    """Stage 4: Schedule background memory and reflection work."""
    schedule_background_memory_consolidation(
        user_id=user_id,
        user_message=user_message,
        assistant_response=result.response,
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
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
        raise
    finally:
        if not worker_task.done():
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass


def list_agent_history(user_id: int, db: Session, *, limit: int = 50) -> list[AgentMessage]:
    return list_transcript_messages(
        db,
        user_id=user_id,
        limit=limit,
    )


async def reset_agent_thread(user_id: int, db: Session) -> None:
    reset_thread(db, user_id)
    db.commit()
    companion = get_companion()
    if companion is not None and companion.user_id == user_id:
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
