from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from anima_server.config import settings
from anima_server.models.runtime import RuntimeMessage, RuntimeRun, RuntimeThread
from anima_server.schemas.chat import ChatContextMessage, ChatRequestAttachment, TodayContext
from anima_server.services.agent.attachments import (
    prepare_chat_attachments,
    validate_chat_attachment_inputs,
)
from anima_server.services.agent.client_actions import build_client_action_runtime
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
from anima_server.services.agent.executor import ToolExecutor
from anima_server.services.agent.llm import (
    ContextWindowOverflowError,
    LLMConfigError,
    LLMInvocationError,
    invalidate_llm_cache,
)
from anima_server.services.agent.memory_blocks import (
    MemoryBlock,
    build_runtime_memory_blocks,
    build_turn_memory_blocks,
)
from anima_server.services.agent.model_capabilities import supports_image_input
from anima_server.services.agent.persistence import (
    append_message,
    append_user_message,
    cancel_run,
    clear_approval_checkpoint,
    close_thread,
    count_messages_by_role,
    create_run,
    get_or_create_thread,
    list_transcript_messages,
    load_approval_checkpoint,
    mark_run_failed,
    persist_agent_result,
    save_approval_checkpoint,
)
from anima_server.services.agent.prompt_budget import resolve_context_budget_tokens
from anima_server.services.agent.reflection import schedule_reflection
from anima_server.services.agent.runtime import AgentRuntime, build_loop_runtime
from anima_server.services.agent.runtime_types import (
    DryRunResult,
    StepFailedError,
    StepProgression,
    StopReason,
    ToolCall,
)
from anima_server.services.agent.sequencing import (
    count_persisted_result_messages,
    reserve_message_sequences,
)
from anima_server.services.agent.state import (
    AgentCitation,
    AgentContextFragment,
    AgentResult,
    AgentRetrievalStats,
    AgentRetrievalTrace,
    StoredAttachment,
    StoredMessage,
    attach_serialized_pills,
    deserialize_agent_retrieval,
    extract_stored_retrieval,
    serialize_agent_retrieval,
)
from anima_server.services.agent.streaming import (
    AgentStreamEvent,
    build_approval_pending_event,
    build_cancelled_event,
    build_done_event,
    build_error_event,
    build_run_started_event,
    build_usage_event,
    summarize_usage,
)
from anima_server.services.agent.system_prompt import (
    PromptTemplateError,
    invalidate_system_prompt_template_cache,
)
from anima_server.services.agent.tool_context import (
    ToolContext,
    clear_tool_context,
    set_tool_context,
)
from anima_server.services.agent.tools import get_tools, prepare_action_tool_schemas
from anima_server.services.agent.turn_coordinator import get_thread_lock, get_user_creation_lock
from anima_server.services.data_crypto import df, get_active_dek
from anima_server.services.documents.rag import DocumentRagResult, search_document_chunks
from anima_server.services.health.event_logger import emit as health_emit

logger = logging.getLogger(__name__)


_runner_lock = Lock()
_cached_runner: AgentRuntime | None = None

_background_tasks: set[asyncio.Task[Any]] = set()


def _track_background_task(coro: Awaitable[Any]) -> None:
    """Run *coro* as a fire-and-forget task with a strong reference."""
    task = asyncio.get_running_loop().create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


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


def _rebuild_runner_for_mod_tools() -> None:
    """Invalidate the cached runner so the next turn rebuilds it with mod
    tools that loaded asynchronously after startup."""
    logger.info("Mod tools loaded; rebuilding agent runner to include them")
    invalidate_agent_runtime_cache()


def ensure_agent_ready() -> None:
    # When anima-mod tools load via a background fetch (cold cache on the
    # event loop), the already-built runner must be rebuilt to include them.
    from anima_server.services.agent.tools import set_mod_tools_loaded_callback

    set_mod_tools_loaded_callback(_rebuild_runner_for_mod_tools)
    runner = get_or_build_runner()
    runner.prepare_system_prompt()


def ensure_image_attachments_supported(
    attachments: Sequence[ChatRequestAttachment],
) -> None:
    if not attachments:
        return
    if supports_image_input(
        settings.agent_provider,
        settings.agent_model,
        base_url=settings.agent_base_url,
    ):
        return
    raise LLMConfigError(
        "The selected model cannot process image attachments. "
        "Choose a vision-capable model or remove the image."
    )


def _prepare_image_attachments(
    *,
    user_id: int,
    attachments: Sequence[ChatRequestAttachment],
) -> tuple[StoredAttachment, ...]:
    return prepare_chat_attachments(user_id=user_id, attachments=attachments)


def _delete_prepared_attachments(attachments: Sequence[StoredAttachment]) -> None:
    for attachment in attachments:
        with contextlib.suppress(OSError):
            Path(attachment.path).unlink(missing_ok=True)


def _validate_image_attachment_inputs(
    attachments: Sequence[ChatRequestAttachment],
) -> None:
    ensure_image_attachments_supported(attachments)
    validate_chat_attachment_inputs(attachments)


def invalidate_agent_runtime_cache() -> None:
    global _cached_runner
    with _runner_lock:
        _cached_runner = None
    invalidate_companion()
    invalidate_llm_cache()
    invalidate_system_prompt_template_cache()


async def run_agent(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    source: str | None = None,
    thread_id: int | None = None,
    attachments: Sequence[ChatRequestAttachment] = (),
    document_ids: Sequence[int] = (),
    context_messages: Sequence[ChatContextMessage] = (),
    today_context: TodayContext | None = None,
) -> AgentResult:
    return await _execute_agent_turn(
        user_message,
        user_id,
        db,
        runtime_db,
        source=source,
        thread_id=thread_id,
        attachments=attachments,
        document_ids=document_ids,
        context_messages=context_messages,
        today_context=today_context,
    )


async def cancel_agent_run(run_id: int, user_id: int, runtime_db: Session) -> RuntimeRun | None:
    """Cancel a running agent turn by run id."""
    run = cancel_run(runtime_db, run_id)
    if run is None:
        return None
    companion = get_companion(user_id)
    if companion is not None:
        companion.set_cancel(run_id)
    runtime_db.commit()
    return run


async def dry_run_agent(user_message: str, user_id: int, db: Session, runtime_db: Session) -> DryRunResult:
    """Execute a dry run: build the full prompt but do not call the LLM.

    Does not create any DB records (threads, messages, runs).
    """
    companion = _get_companion(user_id)

    # Look up existing thread without creating one.
    from sqlalchemy import select as sa_select

    thread = runtime_db.scalar(
        sa_select(RuntimeThread).where(
            RuntimeThread.user_id == user_id,
            RuntimeThread.status == "active",
        )
    )

    history: list[StoredMessage] = []
    memory_blocks: tuple[MemoryBlock, ...] = ()
    if thread is not None:
        companion.thread_id = thread.id
        history = companion.ensure_history_loaded(runtime_db, thread_id=thread.id)
        memory_blocks = (
            *companion.ensure_memory_loaded(db, runtime_db=runtime_db),
            *build_turn_memory_blocks(
                db,
                user_id=user_id,
                thread_id=thread.id,
                query=user_message,
                runtime_db=runtime_db,
            ),
        )

    runner = get_or_build_runner()
    client_action_runtime = build_client_action_runtime(user_id)
    prepared_action_schemas: list[dict[str, Any]] = []
    if client_action_runtime is not None:
        prepared_action_schemas = prepare_action_tool_schemas(
            list(client_action_runtime.action_tool_schemas)
        )
    result = await runner.invoke(
        user_message,
        user_id,
        history,
        memory_blocks=memory_blocks,
        dry_run=True,
        extra_tool_schemas=prepared_action_schemas,
    )
    assert isinstance(result, DryRunResult)
    return result


async def approve_or_deny_turn(
    run_id: int,
    user_id: int,
    approved: bool,
    db: Session,
    runtime_db: Session,
    *,
    denial_reason: str | None = None,
    event_callback: Callable[[AgentStreamEvent],
                             Awaitable[None]] | None = None,
) -> AgentResult:
    row = runtime_db.execute(
        select(RuntimeRun.thread_id, RuntimeRun.user_id, RuntimeRun.status).where(
            RuntimeRun.id == run_id
        )
    ).one_or_none()
    if row is None:
        raise ValueError(f"Run {run_id} is not awaiting approval")

    thread_id, run_user_id, status = row
    if run_user_id != user_id:
        raise PermissionError("Not authorized for this run")
    if status != "awaiting_approval":
        raise ValueError(f"Run {run_id} is not awaiting approval")

    async with get_thread_lock(thread_id):
        runtime_db.expire_all()
        return await _approve_or_deny_turn_locked(
            run_id=run_id,
            user_id=user_id,
            approved=approved,
            db=db,
            runtime_db=runtime_db,
            denial_reason=denial_reason,
            event_callback=event_callback,
        )


async def _approve_or_deny_turn_locked(
    run_id: int,
    user_id: int,
    approved: bool,
    db: Session,
    runtime_db: Session,
    *,
    denial_reason: str | None = None,
    event_callback: Callable[[AgentStreamEvent],
                             Awaitable[None]] | None = None,
) -> AgentResult:
    """Resume a turn after an approval decision.

    On approve: execute the pending tool directly, then optionally one LLM
    follow-up.  On deny: inject denial as a tool error and make one LLM
    follow-up so the companion can respond.
    """
    checkpoint = load_approval_checkpoint(runtime_db, run_id)
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
    clear_approval_checkpoint(runtime_db, run, approval_msg)
    runtime_db.flush()

    companion = _get_companion(user_id)
    thread = runtime_db.get(RuntimeThread, run.thread_id)
    if thread is None:
        raise ValueError("Thread not found")
    companion.thread_id = thread.id

    history = companion.ensure_history_loaded(runtime_db, thread_id=thread.id)
    memory_blocks = companion.ensure_memory_loaded(db, runtime_db=runtime_db)
    conversation_turn_count = count_messages_by_role(
        runtime_db, thread.id, "user")

    cancel_event = companion.create_cancel_event(run.id)
    set_tool_context(
        ToolContext(
            db=db,
            runtime_db=runtime_db,
            user_id=user_id,
            thread_id=thread.id,
            run_id=run.id,
        )
    )
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
        mark_run_failed(runtime_db, run, str(exc.cause))
        runtime_db.commit()
        raise exc.cause from exc
    except Exception as exc:
        mark_run_failed(runtime_db, run, str(exc))
        runtime_db.commit()
        raise
    finally:
        companion.clear_cancel_event(run.id)
        clear_tool_context()

    if result.retrieval is None:
        result.retrieval = deserialize_agent_retrieval(
            extract_stored_retrieval(approval_msg.content_json)
        )

    # Handle cancellation during resume
    if result.stop_reason == StopReason.CANCELLED.value:
        cancel_run(runtime_db, run.id)
        runtime_db.commit()
        if event_callback is not None:
            await event_callback(build_cancelled_event(run.id))
        return result

    # Persist result
    result_message_count = count_persisted_result_messages(result)
    persist_agent_result(
        runtime_db,
        thread=thread,
        run=run,
        result=result,
        initial_sequence_id=(
            reserve_message_sequences(
                runtime_db,
                thread_id=thread.id,
                count=result_message_count,
            )
            if result_message_count > 0
            else None
        ),
        record_feedback=False,
    )
    compact_thread_context(
        runtime_db,
        thread=thread,
        run_id=run.id,
        trigger_token_limit=max(
            1,
            int(resolve_context_budget_tokens() *
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
    runtime_db.commit()
    _refresh_companion_history(
        user_id=user_id,
        runtime_db=runtime_db,
        thread_id=thread.id,
    )

    # Post-turn hooks
    _run_post_turn_hooks(
        user_id=user_id,
        thread_id=thread.id,
        conversation_turn_count=conversation_turn_count,
        user_message="",  # no new user message on resume
        result=result,
        db_factory=_build_db_factory(db),
        runtime_db_factory=_build_runtime_db_factory(),
    )

    if event_callback is not None:
        usage = summarize_usage(result)
        if usage is not None:
            await event_callback(build_usage_event(usage))
        await event_callback(build_done_event(result, thread_id=thread.id))
    return result


async def stream_approve_or_deny(
    run_id: int,
    user_id: int,
    approved: bool,
    db: Session,
    runtime_db: Session,
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
                runtime_db,
                denial_reason=denial_reason,
                event_callback=emit,
            )
        except Exception as exc:
            logger.exception(
                "Approval resume failed for run %s (user %s)", run_id, user_id)
            await queue.put(build_error_event(client_error_message(exc)))
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
    runtime_db: Session,
    *,
    thread_id: int | None = None,
    event_callback: Callable[[AgentStreamEvent],
                             Awaitable[None]] | None = None,
    source: str | None = None,
    tool_delegate: Callable[..., Awaitable[Any]] | None = None,
    delegated_tool_names: frozenset[str] = frozenset(),
    extra_tool_schemas: list[dict[str, Any]] | None = None,
    attachments: Sequence[ChatRequestAttachment] = (),
    document_ids: Sequence[int] = (),
    context_messages: Sequence[ChatContextMessage] = (),
    today_context: TodayContext | None = None,
) -> AgentResult:
    client_action_runtime = None
    if tool_delegate is None:
        client_action_runtime = build_client_action_runtime(user_id)
        if client_action_runtime is not None:
            tool_delegate = client_action_runtime.delegate
            delegated_tool_names = client_action_runtime.delegated_tool_names
            extra_tool_schemas = list(client_action_runtime.action_tool_schemas)

    try:
        if thread_id is not None:
            thread_lock = get_thread_lock(thread_id)
            async with thread_lock:
                return await _execute_agent_turn_locked(
                    user_message,
                    user_id,
                    db,
                    runtime_db,
                    thread_id=thread_id,
                    event_callback=event_callback,
                    source=source,
                    tool_delegate=tool_delegate,
                    delegated_tool_names=delegated_tool_names,
                    extra_tool_schemas=extra_tool_schemas,
                    attachments=attachments,
                    document_ids=document_ids,
                    context_messages=context_messages,
                    today_context=today_context,
                )
        else:
            # Hold the creation lock through thread lock acquisition so that
            # concurrent first-turn requests can't race on get_or_create_thread()
            # with uncommitted DB sessions.
            async with get_user_creation_lock(user_id):
                resolved_thread_id = _resolve_thread_id(user_id, runtime_db)
                thread_lock = get_thread_lock(resolved_thread_id)
                async with thread_lock:
                    return await _execute_agent_turn_locked(
                        user_message,
                        user_id,
                        db,
                        runtime_db,
                        event_callback=event_callback,
                        source=source,
                        tool_delegate=tool_delegate,
                        delegated_tool_names=delegated_tool_names,
                        extra_tool_schemas=extra_tool_schemas,
                        attachments=attachments,
                        document_ids=document_ids,
                        context_messages=context_messages,
                        today_context=today_context,
                    )
    finally:
        if client_action_runtime is not None:
            client_action_runtime.close()


def _resolve_thread_id(user_id: int, runtime_db: Session) -> int:
    """Resolve the main conversation thread ID for lock acquisition."""
    thread = get_or_create_thread(runtime_db, user_id)
    return thread.id


async def _execute_agent_turn_locked(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    thread_id: int | None = None,
    event_callback: Callable[[AgentStreamEvent],
                             Awaitable[None]] | None = None,
    source: str | None = None,
    tool_delegate: Callable[..., Awaitable[Any]] | None = None,
    delegated_tool_names: frozenset[str] = frozenset(),
    extra_tool_schemas: list[dict[str, Any]] | None = None,
    attachments: Sequence[ChatRequestAttachment] = (),
    document_ids: Sequence[int] = (),
    context_messages: Sequence[ChatContextMessage] = (),
    today_context: TodayContext | None = None,
) -> AgentResult:
    # Stage 1: Prepare turn context
    thread, run, user_msg, initial_sequence_id, turn_ctx = await _prepare_turn_context(
        user_message,
        user_id,
        db,
        runtime_db,
        thread_id=thread_id,
        event_callback=event_callback,
        source=source,
        attachments=attachments,
        document_ids=document_ids,
        context_messages=context_messages,
        today_context=today_context,
    )

    # The run row is committed; tell streaming clients the id so they can
    # cancel mid-turn.
    if event_callback is not None:
        await event_callback(
            build_run_started_event(run_id=run.id, thread_id=thread.id))

    # Stage 1b: Proactive context management — compact before the LLM call
    # if estimated context usage already exceeds the threshold.
    try:
        turn_ctx = await _proactive_compact_if_needed(
            runtime_db,
            thread=thread,
            run=run,
            turn_ctx=turn_ctx,
            user_id=user_id,
        )
    except Exception as exc:
        _fail_turn_setup(
            runtime_db,
            run=run,
            user_msg=user_msg,
            context_messages=turn_ctx.context_messages,
            exc=exc,
        )
        raise

    # Stage 2: Invoke the runtime
    companion = _get_companion(user_id)
    try:
        cancel_event = companion.create_cancel_event(run.id)
        result = await _invoke_turn_runtime(
            user_message,
            user_id,
            db,
            runtime_db,
            thread=thread,
            run=run,
            user_msg=user_msg,
            turn_ctx=turn_ctx,
            event_callback=event_callback,
            cancel_event=cancel_event,
            tool_delegate=tool_delegate,
            delegated_tool_names=delegated_tool_names,
            extra_tool_schemas=extra_tool_schemas,
        )
    except asyncio.CancelledError:
        companion.set_cancel(run.id)
        cancel_run(runtime_db, run.id)
        runtime_db.commit()
        raise
    finally:
        companion.clear_cancel_event(run.id)

    result.retrieval = turn_ctx.retrieval

    # Handle cancellation: persist cancel status and emit event
    if result.stop_reason == StopReason.CANCELLED.value:
        cancel_run(runtime_db, run.id)
        runtime_db.commit()
        if event_callback is not None:
            await event_callback(build_cancelled_event(run.id))
        return result

    # Handle approval: persist checkpoint and emit event
    if result.stop_reason == StopReason.AWAITING_APPROVAL.value:
        pending_tc = _persist_approval_checkpoint(
            runtime_db,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=initial_sequence_id,
        )
        _refresh_companion_history(
            user_id=user_id,
            runtime_db=runtime_db,
            thread_id=thread.id,
        )
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
            await event_callback(build_done_event(result, thread_id=thread.id))
        return result

    # Stage 3: Persist result.  The run/user message are already committed
    # (early-commit), so a failure here must mark the run failed and evict
    # the user message — otherwise the run stays "running" forever and the
    # message replays as unanswered history next turn.
    try:
        await _persist_turn_result(
            runtime_db,
            thread=thread,
            run=run,
            result=result,
            initial_sequence_id=initial_sequence_id,
        )
    except Exception as exc:
        _fail_turn_setup(
            runtime_db,
            run=run,
            user_msg=user_msg,
            context_messages=turn_ctx.context_messages,
            exc=exc,
        )
        raise
    _refresh_companion_history(
        user_id=user_id,
        runtime_db=runtime_db,
        thread_id=thread.id,
    )

    # Stage 4: Post-turn hooks
    source_message_ids = _source_message_ids_for_extraction(
        runtime_db,
        user_msg=user_msg,
        run=run,
    )
    _run_post_turn_hooks(
        user_id=user_id,
        thread_id=thread.id,
        conversation_turn_count=turn_ctx.conversation_turn_count,
        user_message=user_message,
        result=result,
        db_factory=_build_db_factory(db),
        runtime_db_factory=_build_runtime_db_factory(),
        source_message_ids=source_message_ids,
    )

    if event_callback is not None:
        usage = summarize_usage(result)
        if usage is not None:
            await event_callback(build_usage_event(usage))
        await event_callback(build_done_event(result, thread_id=thread.id))
    return result


@dataclass(slots=True)
class _TurnContext:
    history: list[StoredMessage]
    conversation_turn_count: int
    memory_blocks: tuple[MemoryBlock, ...]
    attachments: tuple[StoredAttachment, ...] = ()
    context_messages: tuple[RuntimeMessage, ...] = ()
    retrieval: AgentRetrievalTrace | None = None


async def _consolidate_displaced_threads(
    thread_ids: Sequence[int],
    *,
    user_id: int,
    db: Session,
    runtime_db: Session,
) -> None:
    """Fire consolidation for threads closed to keep a single active thread.

    Mirrors ``reset_agent_thread``: commit the close first so the consolidation
    worker's own session sees it, then run it (awaited on sqlite, scheduled
    otherwise).
    """
    if not thread_ids:
        return

    from anima_server.services.agent.eager_consolidation import on_thread_close

    runtime_db.commit()
    soul_db_factory = _build_db_factory(db)
    for old_id in thread_ids:
        close_task = on_thread_close(
            thread_id=old_id,
            user_id=user_id,
            runtime_db_factory=_build_runtime_db_factory(),
            soul_db_factory=soul_db_factory,
        )
        if _runtime_db_is_sqlite(runtime_db):
            await close_task
        else:
            try:
                asyncio.get_running_loop().create_task(close_task)
            except RuntimeError:
                await close_task


async def _prepare_turn_context(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    thread_id: int | None = None,
    event_callback: Callable[[AgentStreamEvent],
                             Awaitable[None]] | None = None,
    source: str | None = None,
    attachments: Sequence[ChatRequestAttachment] = (),
    document_ids: Sequence[int] = (),
    context_messages: Sequence[ChatContextMessage] = (),
    today_context: TodayContext | None = None,
) -> tuple[RuntimeThread, RuntimeRun, RuntimeMessage, int, _TurnContext]:
    """Stage 1: Load thread, persist user message, build memory context.

    Uses the AnimaCompanion cache for static memory blocks and conversation
    history.  Only semantic retrieval (query-dependent) is executed per-turn.
    """
    from anima_server.services.agent.thread_manager import (
        maybe_set_thread_title,
        reactivate_thread_if_needed,
    )

    _validate_image_attachment_inputs(attachments)
    companion = _get_companion(user_id)

    if thread_id is not None:
        thread = runtime_db.get(RuntimeThread, thread_id)
        if thread is None or thread.user_id != user_id:
            raise ValueError(
                f"Thread {thread_id} not found for user {user_id}")
        if thread.status != "active":
            dek = get_active_dek(user_id, "conversations")
            displaced_thread_ids = reactivate_thread_if_needed(
                runtime_db,
                thread=thread,
                user_id=user_id,
                transcripts_dir=settings.data_dir / "transcripts",
                dek=dek,
            )
            runtime_db.flush()
            await _consolidate_displaced_threads(
                displaced_thread_ids,
                user_id=user_id,
                db=db,
                runtime_db=runtime_db,
            )
    else:
        thread = get_or_create_thread(runtime_db, user_id)

    maybe_set_thread_title(thread, user_message)
    prev_thread_id = companion.thread_id
    companion.thread_id = thread.id
    if prev_thread_id != thread.id:
        companion.invalidate_history(thread_id=thread.id)

    # Use cached conversation history when available, otherwise load from DB.
    history = list(companion.ensure_history_loaded(runtime_db, thread_id=thread.id))

    prepared_attachments = _prepare_image_attachments(
        user_id=user_id,
        attachments=attachments,
    )
    cleaned_context_messages = [
        (message, message.content.strip())
        for message in context_messages
        if message.content.strip()
    ]
    try:
        run = create_run(
            runtime_db,
            thread_id=thread.id,
            user_id=user_id,
            provider=settings.agent_provider,
            model=settings.agent_model,
            mode="streaming" if event_callback is not None else "blocking",
        )
        context_sequence_count = len(cleaned_context_messages)
        initial_sequence_id = reserve_message_sequences(
            runtime_db,
            thread_id=thread.id,
            count=context_sequence_count + 1,
        )
        persisted_context_messages: list[RuntimeMessage] = []
        for offset, (context_message, cleaned_content) in enumerate(
            cleaned_context_messages
        ):
            content_json = attach_serialized_pills(
                None,
                [pill.model_dump() for pill in context_message.pills],
            )
            persisted = append_message(
                runtime_db,
                thread=thread,
                run_id=None,
                step_id=None,
                sequence_id=initial_sequence_id + offset,
                role=context_message.role,
                content_text=cleaned_content,
                content_json=content_json,
                source=context_message.source,
            )
            persisted_context_messages.append(persisted)
            history.append(
                StoredMessage(
                    role=context_message.role,
                    content=cleaned_content,
                )
            )
        user_msg = append_user_message(
            runtime_db,
            thread=thread,
            run_id=run.id,
            content=user_message,
            sequence_id=initial_sequence_id + context_sequence_count,
            source=source,
            attachments=prepared_attachments,
        )
        conversation_turn_count = count_messages_by_role(
            runtime_db, thread.id, "user")
    except Exception:
        _delete_prepared_attachments(prepared_attachments)
        raise

    # Commit the run and user message now: this makes the run visible to
    # the cancel endpoint while the turn is in flight, and releases the
    # thread-row lock taken by reserve_message_sequences (otherwise held
    # across the LLM call, blocking /chat/reset and background writers).
    runtime_db.commit()

    try:
        turn_ctx = await _assemble_turn_context(
            user_message=user_message,
            user_id=user_id,
            db=db,
            runtime_db=runtime_db,
            thread=thread,
            companion=companion,
            history=history,
            conversation_turn_count=conversation_turn_count,
            prepared_attachments=prepared_attachments,
            document_ids=document_ids,
            persisted_context_messages=persisted_context_messages,
            today_context=today_context,
        )
    except Exception as exc:
        _fail_turn_setup(
            runtime_db,
            run=run,
            user_msg=user_msg,
            context_messages=persisted_context_messages,
            exc=exc,
        )
        raise
    return thread, run, user_msg, initial_sequence_id, turn_ctx


def _fail_turn_setup(
    runtime_db: Session,
    *,
    run: RuntimeRun,
    user_msg: RuntimeMessage,
    context_messages: Sequence[RuntimeMessage] = (),
    exc: BaseException,
) -> None:
    """Best-effort cleanup when a turn fails after the run and user message
    were committed (early-commit in turn preparation) but before the run
    reached a terminal state.

    Evicts the orphaned user message (and any context messages) from
    active context and marks the run failed, so the run does not stay
    "running" forever and the message does not replay as unanswered
    history on the next turn.

    Rolls back any uncommitted partial work first, then re-reads the run
    from committed state and acts ONLY if it is still "running" — so it is
    safe to call from any failure path, including ones where a downstream
    handler (Stage 2's own cleanup, an approval checkpoint, or a
    successful persist) already moved the run to a terminal/awaiting
    state.
    """
    try:
        # Discard any partial uncommitted state from the failed operation
        # so we act on the committed run/message rows.
        with contextlib.suppress(Exception):
            runtime_db.rollback()
        runtime_db.refresh(run)
        if run.status != "running":
            return
        user_msg.is_in_context = False
        runtime_db.add(user_msg)
        for context_message in context_messages:
            context_message.is_in_context = False
            runtime_db.add(context_message)
        mark_run_failed(runtime_db, run, str(exc))
        runtime_db.commit()
    except Exception:
        logger.exception(
            "Failed to clean up run %s after turn-setup failure", run.id)
        with contextlib.suppress(Exception):
            runtime_db.rollback()


async def _assemble_turn_context(
    *,
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    thread: RuntimeThread,
    companion: AnimaCompanion,
    history: list[StoredMessage],
    conversation_turn_count: int,
    prepared_attachments: tuple[StoredAttachment, ...],
    document_ids: Sequence[int],
    persisted_context_messages: list[RuntimeMessage],
    today_context: TodayContext | None,
) -> _TurnContext:
    """Soul Writer check, semantic retrieval, memory blocks, feedback signals."""
    # Pre-turn Soul Writer check: promote pending core-memory ops (fast,
    # non-LLM) so the current turn sees the freshest soul data.  Candidate
    # promotion makes per-candidate LLM extraction calls, so it runs in the
    # background instead of blocking time-to-first-token; unpromoted
    # candidates stay visible via the pending_memory_updates block.
    try:
        from anima_server.services.agent.candidate_ops import count_eligible_candidates
        from anima_server.services.agent.pending_ops import count_pending_ops
        from anima_server.services.agent.soul_writer import run_soul_writer

        pending = count_pending_ops(runtime_db, user_id=user_id)
        if pending > 0:
            await run_soul_writer(user_id, ops_only=True)
        eligible = count_eligible_candidates(runtime_db, user_id=user_id)
        if eligible > 0:
            _track_background_task(run_soul_writer(user_id))
    except Exception:
        logger.debug("Pre-turn Soul Writer check failed for user %s",
                     user_id, exc_info=True)

    # Semantic retrieval is always per-turn (query-dependent).
    semantic_results: list[tuple[int, str, float]] | None = None
    retrieval_trace: AgentRetrievalTrace | None = None
    query_embedding: list[float] | None = None

    try:
        from anima_server.services.agent.embeddings import (
            AdaptiveRetrievalConfig,
            adaptive_filter_with_stats,
            hybrid_search,
        )

        retrieval_started = time.monotonic()
        search_result = await hybrid_search(
            db,
            user_id=user_id,
            query=user_message,
            limit=15,
            similarity_threshold=0.25,
            runtime_db=runtime_db,
            recency_heat_blend=True,
        )
        retrieval_ms = (time.monotonic() - retrieval_started) * 1000.0
        if query_embedding is None:
            query_embedding = search_result.query_embedding
        if search_result.items:
            adaptive_result = adaptive_filter_with_stats(
                search_result.items,
                config=AdaptiveRetrievalConfig.combined(
                    max_results=12,
                    min_results=3,
                    relative_threshold=0.5,
                    max_drop_ratio=0.35,
                    absolute_min=0.2,
                ),
            )
            filtered = adaptive_result.results
            logger.debug(
                "Adaptive retrieval kept %s/%s memory hits for user %s (%s)",
                adaptive_result.stats.returned,
                adaptive_result.stats.total_considered,
                user_id,
                adaptive_result.stats.triggered_by,
            )
            semantic_results = []
            citations: list[AgentCitation] = []
            context_fragments: list[AgentContextFragment] = []
            for index, (item, score) in enumerate(filtered, start=1):
                content = df(user_id, item.content, table="memory_items", field="content")
                semantic_results.append((item.id, content, score))
                citations.append(
                    AgentCitation(
                        index=index,
                        memory_item_id=item.id,
                        uri=_memory_item_uri(item.id),
                        score=score,
                        category=item.category,
                    )
                )
                context_fragments.append(
                    AgentContextFragment(
                        rank=index,
                        memory_item_id=item.id,
                        uri=_memory_item_uri(item.id),
                        text=content,
                        score=score,
                        category=item.category,
                    )
                )

            retrieval_trace = AgentRetrievalTrace(
                retriever="hybrid",
                citations=tuple(citations),
                context_fragments=tuple(context_fragments),
                stats=AgentRetrievalStats(
                    retrieval_ms=round(retrieval_ms, 3),
                    total_considered=adaptive_result.stats.total_considered,
                    returned=adaptive_result.stats.returned,
                    cutoff_index=adaptive_result.stats.cutoff_index,
                    cutoff_score=adaptive_result.stats.cutoff_score,
                    top_score=adaptive_result.stats.top_score,
                    cutoff_ratio=adaptive_result.stats.cutoff_ratio,
                    triggered_by=adaptive_result.stats.triggered_by,
                ),
            )
    except Exception:
        logger.debug(
            "Hybrid retrieval failed for user %s thread %s",
            user_id,
            thread.id,
            exc_info=True,
        )

    # Static identity blocks come from the companion cache (version-counter
    # invalidated); only the query-ranked and volatile blocks are rebuilt
    # per turn.
    static_blocks = companion.ensure_memory_loaded(db, runtime_db=runtime_db)
    turn_blocks = build_turn_memory_blocks(
        db,
        user_id=user_id,
        thread_id=thread.id,
        semantic_results=semantic_results,
        query_embedding=query_embedding,
        query=user_message,
        runtime_db=runtime_db,
    )
    memory_blocks = (*static_blocks, *turn_blocks)

    document_context_block = _build_document_context_block(
        runtime_db,
        user_id=user_id,
        user_message=user_message,
        document_ids=document_ids,
    )
    if document_context_block is not None:
        memory_blocks = (*memory_blocks, document_context_block)

    today_context_block = _build_today_context_block(today_context)
    if today_context_block is not None:
        memory_blocks = (*memory_blocks, today_context_block)

    # Feedback signals (best-effort)
    try:
        from anima_server.services.agent.feedback_signals import (
            apply_memory_correction,
            collect_feedback_signals,
            record_feedback_signals,
        )

        signals = collect_feedback_signals(
            user_id=user_id,
            user_message=user_message,
            thread_id=thread.id,
            runtime_db=runtime_db,
        )
        if signals:
            record_feedback_signals(
                db, user_id=user_id, signals=signals, runtime_db=runtime_db,
            )
            # When a correction is detected, fix the underlying memory
            if any(s.signal_type == "correction" for s in signals):
                apply_memory_correction(
                    db,
                    user_id=user_id,
                    user_message=user_message,
                    thread_id=thread.id,
                    runtime_db=runtime_db,
                )
    except Exception:
        logger.warning(
            "Feedback signal processing failed for user %s", user_id,
            exc_info=True,
        )

    # Memory pressure warning: estimate total context usage and inject
    # a warning block when approaching the context window limit.
    memory_blocks = _inject_memory_pressure_warning(
        memory_blocks,
        history,
        companion,
    )

    return _TurnContext(
        history=history,
        conversation_turn_count=conversation_turn_count,
        memory_blocks=memory_blocks,
        attachments=prepared_attachments,
        context_messages=tuple(persisted_context_messages),
        retrieval=retrieval_trace,
    )


def _build_document_context_block(
    runtime_db: Session,
    *,
    user_id: int,
    user_message: str,
    document_ids: Sequence[int],
) -> MemoryBlock | None:
    cleaned_document_ids = _dedupe_positive_ids(document_ids)
    query = user_message.strip()
    if not cleaned_document_ids or not query:
        return None

    try:
        results = search_document_chunks(
            runtime_db,
            user_id,
            query,
            document_ids=cleaned_document_ids,
            limit=5,
        )
    except Exception:
        logger.debug(
            "Document retrieval failed for user %s documents %s",
            user_id,
            cleaned_document_ids,
            exc_info=True,
        )
        return None
    if not results:
        return None

    lines = [
        "Selected document context from indexed PDFs. Use this only when it is relevant.",
    ]
    for index, result in enumerate(results, start=1):
        location = _format_document_location(result)
        section = f", section {result.section_title}" if result.section_title else ""
        lines.append(
            f"[{index}] {result.filename}{location}{section} "
            f"(document {result.document_id}, chunk {result.chunk_id}, relevance {result.similarity:.2f})"
        )
        lines.append(_truncate_document_chunk(result.content))

    return MemoryBlock(
        label="document_context",
        value="\n".join(lines),
        description=(
            "Query-relevant excerpts from PDFs the user explicitly selected for this chat turn. "
            "Ground answers in these snippets when they apply; do not treat them as long-term memory."
        ),
    )


def _dedupe_positive_ids(ids: Sequence[int]) -> list[int]:
    cleaned: list[int] = []
    seen: set[int] = set()
    for raw_id in ids:
        try:
            document_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if document_id <= 0 or document_id in seen:
            continue
        seen.add(document_id)
        cleaned.append(document_id)
    return cleaned


def _format_document_location(result: DocumentRagResult) -> str:
    if result.page_start is None:
        return ""
    if result.page_end is None or result.page_end == result.page_start:
        return f", page {result.page_start}"
    return f", pages {result.page_start}-{result.page_end}"


def _truncate_document_chunk(content: str, limit: int = 900) -> str:
    cleaned = " ".join(content.split())
    if len(cleaned) <= limit:
        return cleaned
    truncated = cleaned[:limit]
    boundary = max(truncated.rfind(". "), truncated.rfind("; "), truncated.rfind(", "))
    if boundary >= limit // 2:
        return truncated[: boundary + 1]
    return truncated.rstrip()


def _build_today_context_block(today_context: TodayContext | None) -> MemoryBlock | None:
    if today_context is None:
        return None

    lines = ["Current user state for today:"]
    mood = (today_context.mood or "").strip()
    energy = (today_context.energy or "").strip()
    note = (today_context.note or "").strip()
    if mood:
        lines.append(f"- Mood: {mood}")
    if energy:
        lines.append(f"- Energy: {energy}")
    if note:
        lines.append(f"- Note: {note}")

    return MemoryBlock(
        label="today_user_context",
        value="\n".join(lines),
        description=(
            "User-authored temporary context for today. Use it to adapt tone, "
            "pacing, and suggestions. When the mood or energy is relevant, you may "
            "gently ask one short check-in or offer a lower-friction path, but not "
            "every turn. Do not store it as memory, do not diagnose it, and do not "
            "repeat it unless useful or asked."
        ),
        read_only=True,
    )


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

    threshold = int(resolve_context_budget_tokens() * _MEMORY_PRESSURE_RATIO)

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
    runtime_db: Session,
    *,
    thread: RuntimeThread,
    run: RuntimeRun,
    user_msg: RuntimeMessage,
    turn_ctx: _TurnContext,
    event_callback: Callable[[AgentStreamEvent],
                             Awaitable[None]] | None = None,
    cancel_event: asyncio.Event | None = None,
    tool_delegate: Callable[..., Awaitable[Any]] | None = None,
    delegated_tool_names: frozenset[str] = frozenset(),
    extra_tool_schemas: list[dict[str, Any]] | None = None,
) -> AgentResult:
    """Stage 2: Set tool context and invoke the agent runtime."""
    set_tool_context(
        ToolContext(
            db=db,
            runtime_db=runtime_db,
            user_id=user_id,
            thread_id=thread.id,
            run_id=run.id,
        )
    )

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
            runtime_db=runtime_db,
        )

    try:
        runner = get_or_build_runner()
        tool_executor: ToolExecutor | None = None

        prepared_action_schemas: list[dict[str, Any]] = []
        if extra_tool_schemas:
            prepared_action_schemas = prepare_action_tool_schemas(
                extra_tool_schemas)
        if tool_delegate:
            tool_executor = ToolExecutor(
                get_tools(),
                delegate=tool_delegate,
                delegated_tool_names=delegated_tool_names,
            )

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
                extra_tool_schemas=prepared_action_schemas,
                tool_executor=tool_executor,
                user_attachments=turn_ctx.attachments,
            )
        except StepFailedError as exc:
            if not _should_retry_after_compaction(exc):
                raise
            health_emit("llm", "context_overflow", "warn", user_id=user_id)
            # Context overflow: compact and retry once.
            compacted = _emergency_compact(runtime_db, thread=thread, run=run)
            if not compacted:
                raise
            logger.info(
                "Context overflow detected — compacted %d messages, retrying",
                compacted.compacted_message_count,
            )
            health_emit("llm", "compaction", "info", user_id=user_id, data={
                "compacted_messages": compacted.compacted_message_count,
            })

            # Post-compaction: promote pending candidates
            try:
                from anima_server.services.agent.soul_writer import run_soul_writer

                await run_soul_writer(user_id)
            except Exception:
                logger.debug(
                    "Post-emergency-compaction Soul Writer failed", exc_info=True)

            turn_ctx = _rebuild_turn_context_after_compaction(
                runtime_db,
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
                extra_tool_schemas=prepared_action_schemas,
                tool_executor=tool_executor,
                user_attachments=turn_ctx.attachments,
            )
    except StepFailedError as exc:
        _handle_step_failure(
            runtime_db,
            run=run,
            user_msg=user_msg,
            err=exc,
            context_messages=turn_ctx.context_messages,
        )
        raise exc.cause from exc
    except Exception as exc:
        # Remove orphaned user message from active context so it doesn't
        # replay as valid history on the next turn.
        user_msg.is_in_context = False
        runtime_db.add(user_msg)
        for context_message in turn_ctx.context_messages:
            context_message.is_in_context = False
            runtime_db.add(context_message)
        mark_run_failed(runtime_db, run, str(exc))
        runtime_db.commit()
        raise
    finally:
        clear_tool_context()


async def _proactive_compact_if_needed(
    runtime_db: Session,
    *,
    thread: RuntimeThread,
    run: RuntimeRun,
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

    threshold = int(resolve_context_budget_tokens() *
                    settings.agent_compaction_trigger_ratio)
    if estimated_tokens <= threshold:
        return turn_ctx

    logger.info(
        "Proactive compaction: estimated %d tokens > threshold %d",
        estimated_tokens,
        threshold,
    )
    result = compact_thread_context(
        runtime_db,
        thread=thread,
        run_id=run.id,
        trigger_token_limit=threshold,
        keep_last_messages=max(
            1, settings.agent_compaction_keep_last_messages),
        reserved_prompt_tokens=block_chars // 4,
    )
    if result is None:
        return turn_ctx

    runtime_db.flush()
    logger.info(
        "Proactive compaction: %d messages compacted (%d -> %d estimated tokens)",
        result.compacted_message_count,
        result.estimated_tokens_before,
        result.estimated_tokens_after,
    )

    # Post-compaction: promote pending candidates so the rebuilt context
    # reflects the latest soul state.
    try:
        from anima_server.services.agent.soul_writer import run_soul_writer

        await run_soul_writer(user_id)
    except Exception:
        logger.debug("Post-compaction Soul Writer failed", exc_info=True)

    return _rebuild_turn_context_after_compaction(
        runtime_db,
        user_id=user_id,
        thread=thread,
        user_message="",
        turn_ctx=turn_ctx,
    )


def _should_retry_after_compaction(exc: StepFailedError) -> bool:
    """Return True if the step failure is a context overflow that is safe
    to retry.

    The retry re-runs the entire turn, so it is only safe when no tools
    have executed yet — otherwise side-effecting tools (save_to_memory,
    create_task, delegated client tools) would run a second time.  An
    overflow on the very first LLM request (prompt too large from the
    start) is exactly the case compaction can fix.
    """
    if not settings.agent_context_overflow_retry:
        return False
    if not isinstance(exc.cause, ContextWindowOverflowError):
        return False
    return (
        exc.context.step_index == 0
        and exc.progression < StepProgression.TOOLS_STARTED
    )


def _emergency_compact(
    runtime_db: Session,
    *,
    thread: RuntimeThread,
    run: RuntimeRun,
) -> CompactionResult | None:
    """Run compaction mid-turn to recover from context overflow.

    Uses aggressive settings: keep fewer messages and reserve no prompt
    tokens (since the overflow already happened).
    """
    keep_last = max(1, settings.agent_compaction_keep_last_messages // 2)
    result = compact_thread_context(
        runtime_db,
        thread=thread,
        run_id=run.id,
        trigger_token_limit=1,  # force compaction
        keep_last_messages=keep_last,
        reserved_prompt_tokens=0,
    )
    if result is not None:
        runtime_db.flush()
    return result


def _rebuild_turn_context_after_compaction(
    runtime_db: Session,
    *,
    user_id: int,
    thread: RuntimeThread,
    user_message: str,
    turn_ctx: _TurnContext,
) -> _TurnContext:
    """Reload history and memory after emergency compaction."""
    companion = _get_companion(user_id)
    companion.invalidate_history(thread_id=thread.id)
    history = companion.ensure_history_loaded(runtime_db, thread_id=thread.id)
    conversation_turn_count = count_messages_by_role(
        runtime_db, thread.id, "user")
    return _TurnContext(
        history=history,
        conversation_turn_count=conversation_turn_count,
        memory_blocks=turn_ctx.memory_blocks,
        attachments=turn_ctx.attachments,
        context_messages=turn_ctx.context_messages,
        retrieval=turn_ctx.retrieval,
    )


def _memory_item_uri(memory_item_id: int) -> str:
    return f"memory://items/{memory_item_id}"


def _refresh_companion_history(
    *,
    user_id: int,
    runtime_db: Session,
    thread_id: int,
) -> None:
    """Reload the cached conversation window from persisted in-context history."""
    companion = get_companion(user_id)
    if companion is None:
        return
    companion.invalidate_history(thread_id=thread_id)
    companion.ensure_history_loaded(runtime_db, thread_id=thread_id)


def _handle_step_failure(
    runtime_db: Session,
    *,
    run: RuntimeRun,
    user_msg: RuntimeMessage,
    err: StepFailedError,
    context_messages: Sequence[RuntimeMessage] = (),
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
    runtime_db.add(user_msg)
    for context_message in context_messages:
        context_message.is_in_context = False
        runtime_db.add(context_message)

    detail = f"step {err.context.step_index} failed at {stage.name}: {err.cause}"
    mark_run_failed(runtime_db, run, detail)
    runtime_db.commit()


def _persist_approval_checkpoint(
    runtime_db: Session,
    *,
    thread: RuntimeThread,
    run: RuntimeRun,
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
        runtime_db,
        thread=thread,
        run=run,
        result=result,
        initial_sequence_id=(
            reserve_message_sequences(
                runtime_db,
                thread_id=thread.id,
                count=result_message_count,
            )
            if result_message_count > 0
            else None
        ),
        record_feedback=False,
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
        mark_run_failed(
            runtime_db, run, "Could not reconstruct pending tool call for approval checkpoint")
        runtime_db.commit()
        return None

    seq_id = reserve_message_sequences(
        runtime_db, thread_id=thread.id, count=1)
    save_approval_checkpoint(
        runtime_db,
        thread=thread,
        run=run,
        tool_call=pending_tool_call,
        step_id=None,
        sequence_id=seq_id,
        retrieval=serialize_agent_retrieval(result.retrieval),
    )

    runtime_db.commit()
    return pending_tool_call


async def _persist_turn_result(
    runtime_db: Session,
    *,
    thread: RuntimeThread,
    run: RuntimeRun,
    result: AgentResult,
    initial_sequence_id: int,
) -> None:
    """Stage 3: Write result to DB; schedule compaction in the background.

    Compaction (LLM summarization with a text-based fallback) runs as a
    background task so the client's ``done`` event is not delayed by a
    full non-streaming LLM call.
    """
    result_message_count = count_persisted_result_messages(result)
    persist_agent_result(
        runtime_db,
        thread=thread,
        run=run,
        result=result,
        initial_sequence_id=(
            reserve_message_sequences(
                runtime_db,
                thread_id=thread.id,
                count=result_message_count,
            )
            if result_message_count > 0
            else None
        ),
    )
    runtime_db.commit()

    _track_background_task(
        _compact_thread_in_background(
            user_id=run.user_id,
            thread_id=thread.id,
            run_id=run.id,
            reserved_prompt_tokens=(
                result.prompt_budget.system_prompt_token_estimate
                if result.prompt_budget is not None
                else 0
            ),
        )
    )


async def _compact_thread_in_background(
    *,
    user_id: int,
    thread_id: int,
    run_id: int,
    reserved_prompt_tokens: int,
) -> None:
    """Post-turn compaction off the turn's critical path.

    Waits on the thread lock so it never races a subsequent turn (it is
    scheduled while the current turn still holds the lock, so it runs
    right after the turn completes), then opens a fresh session and
    refreshes the companion history cache if anything was compacted.
    """
    from anima_server.services.agent.compaction import compact_thread_context_with_llm

    try:
        async with get_thread_lock(thread_id):
            factory = _build_runtime_db_factory()
            with factory() as runtime_db:
                thread = runtime_db.get(RuntimeThread, thread_id)
                if thread is None:
                    return
                compaction_kwargs = dict(
                    thread=thread,
                    run_id=run_id,
                    trigger_token_limit=max(
                        1,
                        int(resolve_context_budget_tokens() *
                            settings.agent_compaction_trigger_ratio),
                    ),
                    keep_last_messages=max(
                        1, settings.agent_compaction_keep_last_messages),
                    reserved_prompt_tokens=reserved_prompt_tokens,
                )

                # Try LLM-powered compaction first (best-effort)
                compaction_result = None
                try:
                    compaction_result = await compact_thread_context_with_llm(
                        runtime_db, **compaction_kwargs)
                except Exception:
                    logger.warning(
                        "LLM compaction failed for thread %s; falling back "
                        "to text-based compaction",
                        thread_id,
                        exc_info=True,
                    )

                # Fall back to fast text-based compaction if LLM didn't trigger
                if compaction_result is None:
                    compaction_result = compact_thread_context(
                        runtime_db, **compaction_kwargs)

                runtime_db.commit()
                if compaction_result is not None:
                    _refresh_companion_history(
                        user_id=user_id,
                        runtime_db=runtime_db,
                        thread_id=thread_id,
                    )
    except Exception:
        logger.exception(
            "Post-turn compaction failed for thread %s", thread_id)


def _extract_inner_thoughts(result: AgentResult) -> str:
    """Extract thinking content from step traces for consolidation.

    Sources (with fallback):
    1. reasoning_content — native model reasoning (o1/o3, Claude thinking)
    2. assistant_text — model's intermediate text on non-terminal steps (only if no reasoning_content)
    3. inner_thinking on tool results — backward compat (legacy thinking kwarg)
    """
    thoughts: list[str] = []
    for trace in result.step_traces:
        # 1. Native model reasoning (highest quality signal)
        if trace.reasoning_content:
            thoughts.append(trace.reasoning_content.strip())
        # 2. Fallback: assistant text from intermediate steps
        elif trace.assistant_text and trace.tool_calls:
            thoughts.append(trace.assistant_text.strip())

        # 3. Backward compat: inner_thinking from tool results
        for tr in trace.tool_results:
            if tr.inner_thinking:
                thoughts.append(tr.inner_thinking.strip())

    return "\n".join(thoughts)


def _run_post_turn_hooks(
    *,
    user_id: int,
    thread_id: int,
    conversation_turn_count: int | None,
    user_message: str,
    result: AgentResult,
    db_factory: Callable[[], Session],
    runtime_db_factory: Callable[[], Session],
    source_message_ids: list[int] | None = None,
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
        conversation_turn_count=conversation_turn_count,
        db_factory=db_factory,
        runtime_db_factory=runtime_db_factory,
        source_message_ids=source_message_ids,
    )
    schedule_reflection(
        user_id=user_id,
        thread_id=thread_id,
        db_factory=db_factory,
        runtime_db_factory=runtime_db_factory,
    )


def _source_message_ids_for_extraction(
    runtime_db: Session,
    *,
    user_msg: RuntimeMessage,
    run: RuntimeRun,
) -> list[int]:
    message_ids = [int(user_msg.id)]
    result_message_ids = runtime_db.scalars(
        select(RuntimeMessage.id)
        .where(
            RuntimeMessage.run_id == run.id,
            RuntimeMessage.id != user_msg.id,
            RuntimeMessage.role.in_(("assistant", "tool")),
        )
        .order_by(RuntimeMessage.sequence_id)
    ).all()
    message_ids.extend(int(message_id) for message_id in result_message_ids)
    return message_ids


def client_error_message(exc: Exception) -> str:
    """Client-safe error text: pass through messages written for users,
    mask everything else (provider/DB errors can leak URLs or payloads)."""
    if isinstance(exc, (LLMConfigError, LLMInvocationError, PromptTemplateError, ValueError)):
        return str(exc)
    return "An internal error occurred while processing this message."


async def stream_agent(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    thread_id: int | None = None,
    source: str | None = None,
    tool_delegate: Callable[..., Awaitable[Any]] | None = None,
    delegated_tool_names: frozenset[str] = frozenset(),
    extra_tool_schemas: list[dict[str, Any]] | None = None,
    attachments: Sequence[ChatRequestAttachment] = (),
    document_ids: Sequence[int] = (),
    context_messages: Sequence[ChatContextMessage] = (),
    today_context: TodayContext | None = None,
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
                runtime_db,
                thread_id=thread_id,
                event_callback=emit,
                source=source,
                tool_delegate=tool_delegate,
                delegated_tool_names=delegated_tool_names,
                extra_tool_schemas=extra_tool_schemas,
                attachments=attachments,
                document_ids=document_ids,
                context_messages=context_messages,
                today_context=today_context,
            )
        except Exception as exc:
            logger.exception("Agent turn failed for user %s", user_id)
            await queue.put(build_error_event(client_error_message(exc)))
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


def list_agent_history(user_id: int, runtime_db: Session, *, limit: int = 50) -> list[RuntimeMessage]:
    return list_transcript_messages(
        runtime_db,
        user_id=user_id,
        limit=limit,
    )


async def reset_agent_thread(
    user_id: int,
    runtime_db: Session,
    db: Session | None = None,
) -> None:
    """Rotate to a fresh active thread while preserving the closed thread for archival."""
    thread = runtime_db.scalar(
        select(RuntimeThread).where(
            RuntimeThread.user_id == user_id,
            RuntimeThread.status == "active",
        )
    )

    thread_id: int | None = None
    if thread is not None:
        thread_id = thread.id
        close_thread(runtime_db, thread_id=thread_id)

    get_or_create_thread(runtime_db, user_id)
    runtime_db.commit()

    try:
        if thread_id is not None:
            from anima_server.services.agent.eager_consolidation import on_thread_close

            soul_db_factory = _build_db_factory(db) if db is not None else None
            close_task = on_thread_close(
                thread_id=thread_id,
                user_id=user_id,
                runtime_db_factory=_build_runtime_db_factory(),
                soul_db_factory=soul_db_factory,
            )
            if _runtime_db_is_sqlite(runtime_db):
                await close_task
            else:
                asyncio.get_running_loop().create_task(close_task)
    except RuntimeError:
        pass

    companion = get_companion(user_id)
    if companion is not None:
        companion.reset()


def _runtime_db_is_sqlite(runtime_db: Session) -> bool:
    try:
        return runtime_db.get_bind().dialect.name == "sqlite"
    except Exception:
        return False


def _build_db_factory(db: Session) -> Callable[[], Session]:
    bind = db.get_bind()
    resolved_bind = getattr(bind, "engine", bind)
    return sessionmaker(
        bind=resolved_bind,
        autoflush=db.autoflush,
        expire_on_commit=db.expire_on_commit,
        class_=type(db),
    )


def _build_runtime_db_factory() -> Callable[[], Session]:
    from anima_server.db.runtime import get_runtime_session_factory

    return get_runtime_session_factory()
