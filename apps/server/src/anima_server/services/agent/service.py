from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from anima_server.config import settings
from anima_server.models.runtime import (
    RuntimeDocument,
    RuntimeDocumentChunk,
    RuntimeImageMessageLink,
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeMessage,
    RuntimeRun,
    RuntimeSource,
    RuntimeSourceSpan,
    RuntimeStep,
    RuntimeThread,
)
from anima_server.schemas.chat import (
    ChatContextMessage,
    ChatRequestAttachment,
    MessagePill,
    TodayContext,
)
from anima_server.services.agent.attachments import (
    AttachmentValidationError,
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
    append_corefs_message_reference,
    append_message,
    append_user_message,
    cancel_run,
    clear_approval_checkpoint,
    close_thread,
    count_messages_by_role,
    create_run,
    create_step,
    ensure_runtime_thread_reference,
    finalize_run,
    get_or_create_thread,
    list_transcript_messages,
    load_approval_checkpoint,
    mark_run_failed,
    persist_agent_result,
    save_approval_checkpoint,
)
from anima_server.services.agent.prompt_budget import (
    estimate_char_tokens,
    resolve_context_budget_tokens,
    resolve_document_context_budget_chars,
)
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
    ATTACHMENTS_CONTENT_KEY,
    AgentCitation,
    AgentContextFragment,
    AgentResult,
    AgentRetrievalStats,
    AgentRetrievalTrace,
    StoredAttachment,
    StoredMessage,
    attach_serialized_pills,
    deserialize_agent_retrieval,
    deserialize_stored_attachments,
    extract_stored_pills,
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
    peek_tool_context,
    set_tool_context,
)
from anima_server.services.agent.tools import get_tools, prepare_action_tool_schemas
from anima_server.services.agent.turn_coordinator import get_thread_lock, get_user_creation_lock
from anima_server.services.corefs.sealed_runtime import (
    reseal_runtime_message,
    runtime_private_exact_lookup_value,
)
from anima_server.services.data_crypto import df
from anima_server.services.documents.rag import DocumentRagResult, search_document_chunks
from anima_server.services.documents.store import get_document_for_user, list_document_chunks
from anima_server.services.health.event_logger import emit as health_emit
from anima_server.services.ingestion.retrieval import KnowledgeConceptHit
from anima_server.services.sessions import get_active_dek_async

logger = logging.getLogger(__name__)


_runner_lock = Lock()
_cached_runner: AgentRuntime | None = None

_background_tasks: set[asyncio.Task[Any]] = set()
_DOCUMENT_PILL_KINDS = frozenset({"document_attachment", "document_source"})
_MAX_RECALLED_IMAGE_SOURCE_PILLS = 3


def normalize_document_only_user_message(
    user_message: str,
    document_ids: Sequence[int],
) -> str:
    """Give document-only chat turns an explicit user intent."""
    if user_message.strip() or not _dedupe_positive_ids(document_ids):
        return user_message
    return _default_document_only_user_message(document_ids)


def _default_document_only_user_message(document_ids: Sequence[int]) -> str:
    document_count = len(_dedupe_positive_ids(document_ids))
    noun = "document" if document_count == 1 else "documents"
    return f"Summarize the selected {noun}."


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
    runtime_db: Session | None = None,
) -> tuple[StoredAttachment, ...]:
    return prepare_chat_attachments(
        user_id=user_id,
        attachments=attachments,
        runtime_db=runtime_db,
    )


def _delete_prepared_attachments(attachments: Sequence[StoredAttachment]) -> None:
    for attachment in attachments:
        if not attachment.delete_on_error:
            continue
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
    run = runtime_db.get(RuntimeRun, run_id)
    if run is None:
        return None
    was_active = run.status not in ("completed", "failed", "cancelled")
    run = cancel_run(runtime_db, run_id)
    companion = get_companion(user_id)
    if companion is not None and (was_active or companion.get_cancel_event(run_id) is not None):
        # Only signal when a turn can still observe it: an in-flight turn
        # pops its event in Stage 2's finally, but a pre-set event created
        # for a long-terminal run would sit in the map forever.
        companion.set_cancel(run_id)
    runtime_db.commit()
    return run


async def dry_run_agent(
    user_message: str, user_id: int, db: Session, runtime_db: Session
) -> DryRunResult:
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
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None = None,
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
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None = None,
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
    conversation_turn_count = count_messages_by_role(runtime_db, thread.id, "user")

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

    from anima_server.services.corefs.conversation_authority import (
        active_conversation_authority_session,
    )

    authority_session = active_conversation_authority_session(user_id)
    if authority_session is not None:
        await _persist_corefs_turn_result(
            runtime_db,
            authority_session=authority_session,
            thread=thread,
            run=run,
            result=result,
        )
    else:
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
                int(resolve_context_budget_tokens() * settings.agent_compaction_trigger_ratio),
            ),
            keep_last_messages=max(1, settings.agent_compaction_keep_last_messages),
            reserved_prompt_tokens=(
                result.prompt_budget.system_prompt_token_estimate
                if result.prompt_budget is not None
                else 0
            ),
        )
    runtime_db.commit()
    _index_run_user_image_attachments_inline(runtime_db, user_id=user_id, run=run)
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


async def _stream_via_queue(
    run_turn: Callable[[Callable[[AgentStreamEvent], Awaitable[None]]], Awaitable[Any]],
    *,
    failure_log: str,
) -> AsyncGenerator[AgentStreamEvent, None]:
    """Run ``run_turn`` in a worker task and relay its emitted events as an SSE
    stream through a bounded queue.

    Shared pump for both the live turn (``stream_agent``) and the
    approval-resume turn (``stream_approve_or_deny``): the only per-caller
    differences are which coroutine the worker awaits and the failure log
    line.  The worker never blocks on the end-of-stream sentinel — if the
    consumer stopped reading while the queue was full, an awaited ``put()``
    would deadlock it (and the generator's ``finally`` awaits the worker), so
    it is put best-effort and may be dropped when the queue is full (ARH-002).
    Because a dropped sentinel would otherwise hang the consumer on
    ``queue.get()`` forever, the consumer races each ``get()`` against worker
    completion and drains anything buffered once the worker is done.
    """
    queue: asyncio.Queue[AgentStreamEvent | None] = asyncio.Queue(
        maxsize=settings.agent_stream_queue_max_size,
    )

    async def emit(event: AgentStreamEvent) -> None:
        await queue.put(event)

    async def worker() -> None:
        try:
            await run_turn(emit)
        except Exception as exc:
            logger.exception("%s", failure_log)
            await queue.put(build_error_event(client_error_message(exc)))
        finally:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    worker_task = asyncio.create_task(worker())
    try:
        while True:
            get_task = asyncio.ensure_future(queue.get())
            done, _pending = await asyncio.wait(
                {get_task, worker_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if get_task in done:
                event = get_task.result()
                if event is None:
                    break
                await asyncio.sleep(0)
                yield event
                continue
            # Worker finished (or errored) without the consumer reading a
            # sentinel — it may have been dropped on a full queue.  Cancel the
            # pending get, drain anything still buffered, then stop.
            get_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await get_task
            while not queue.empty():
                buffered = queue.get_nowait()
                if buffered is None:
                    continue
                yield buffered
            break
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

    async def run_turn(emit: Callable[[AgentStreamEvent], Awaitable[None]]) -> None:
        await approve_or_deny_turn(
            run_id,
            user_id,
            approved,
            db,
            runtime_db,
            denial_reason=denial_reason,
            event_callback=emit,
        )

    async with contextlib.aclosing(
        _stream_via_queue(
            run_turn,
            failure_log=f"Approval resume failed for run {run_id} (user {user_id})",
        )
    ) as stream:
        async for event in stream:
            yield event


async def _execute_agent_turn(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    thread_id: int | None = None,
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None = None,
    source: str | None = None,
    tool_delegate: Callable[..., Awaitable[Any]] | None = None,
    delegated_tool_names: frozenset[str] = frozenset(),
    extra_tool_schemas: list[dict[str, Any]] | None = None,
    attachments: Sequence[ChatRequestAttachment] = (),
    document_ids: Sequence[int] = (),
    context_messages: Sequence[ChatContextMessage] = (),
    today_context: TodayContext | None = None,
) -> AgentResult:
    user_message = normalize_document_only_user_message(user_message, document_ids)
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
    from anima_server.services.corefs.conversation_authority import (
        active_conversation_authority_session,
        get_active_canonical_thread,
    )
    from anima_server.services.corefs.conversation_mutations import create_canonical_thread

    session = active_conversation_authority_session(user_id)
    if session is not None:
        view = get_active_canonical_thread(session=session)
        if view is None:
            view = create_canonical_thread(session=session)
        thread_id = view.document.legacy_thread_id
        if isinstance(thread_id, bool) or not isinstance(thread_id, int):
            raise RuntimeError("Canonical thread has no compatible Runtime reference identity.")
        ensure_runtime_thread_reference(
            runtime_db,
            user_id=user_id,
            thread_id=thread_id,
        )
        runtime_db.commit()
        return thread_id
    thread = get_or_create_thread(runtime_db, user_id)
    return thread.id


async def _execute_agent_turn_locked(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    thread_id: int | None = None,
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None = None,
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
    thread, run, user_msg, turn_ctx = await _prepare_turn_context(
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
    # cancel mid-turn.  The emit awaits the stream queue, so a client
    # disconnect can cancel us right here — clean up or the run row stays
    # "running" forever.
    try:
        if event_callback is not None:
            await event_callback(build_run_started_event(run_id=run.id, thread_id=thread.id))

        # Stage 1b: Proactive context management — compact before the LLM
        # call if estimated context usage already exceeds the threshold.
        turn_ctx = await _proactive_compact_if_needed(
            runtime_db,
            thread=thread,
            run=run,
            turn_ctx=turn_ctx,
            user_id=user_id,
        )
    except asyncio.CancelledError as exc:
        _fail_turn_setup(
            runtime_db,
            run=run,
            user_msg=user_msg,
            context_messages=turn_ctx.context_messages,
            exc=exc,
            cancelled=True,
        )
        raise
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
            assistant_pills=_build_assistant_source_pills(
                turn_ctx,
                source_message=user_msg,
            ),
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
            assistant_pills=_build_assistant_source_pills(
                turn_ctx,
                source_message=user_msg,
            ),
        )
    except asyncio.CancelledError as exc:
        _fail_turn_setup(
            runtime_db,
            run=run,
            user_msg=user_msg,
            context_messages=turn_ctx.context_messages,
            exc=exc,
            cancelled=True,
        )
        raise
    except Exception as exc:
        _fail_turn_setup(
            runtime_db,
            run=run,
            user_msg=user_msg,
            context_messages=turn_ctx.context_messages,
            exc=exc,
        )
        raise
    _index_image_attachments_inline(
        runtime_db,
        user_id=user_id,
        user_message=user_message,
        attachments=turn_ctx.attachments,
    )
    runtime_db.commit()
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
        source_run_id=run.id,
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
    has_document_context: bool = False
    document_source_pills: tuple[dict[str, object], ...] = ()
    recalled_image_source_pills: tuple[dict[str, object], ...] = ()


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
                # Strong-ref via the tracked set: the loop keeps only weak
                # task references, so a bare create_task can be GC'd
                # mid-flight and silently never consolidate.
                _track_background_task(close_task)
            except RuntimeError:
                await close_task


async def _prepare_turn_context(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    thread_id: int | None = None,
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None = None,
    source: str | None = None,
    attachments: Sequence[ChatRequestAttachment] = (),
    document_ids: Sequence[int] = (),
    context_messages: Sequence[ChatContextMessage] = (),
    today_context: TodayContext | None = None,
) -> tuple[RuntimeThread, RuntimeRun, RuntimeMessage, _TurnContext]:
    """Stage 1: Load thread, persist user message, build memory context.

    Uses the AnimaCompanion cache for static memory blocks and conversation
    history.  Only semantic retrieval (query-dependent) is executed per-turn.
    """
    from anima_server.services.agent.thread_manager import (
        maybe_set_thread_title,
        reactivate_thread_if_needed,
    )
    from anima_server.services.corefs.conversation_authority import (
        active_conversation_authority_session,
    )

    authority_session = active_conversation_authority_session(user_id)
    if authority_session is not None:
        return await _prepare_corefs_turn_context(
            user_message,
            user_id,
            db,
            runtime_db,
            authority_session=authority_session,
            thread_id=thread_id,
            event_callback=event_callback,
            source=source,
            attachments=attachments,
            document_ids=document_ids,
            context_messages=context_messages,
            today_context=today_context,
        )

    _validate_image_attachment_inputs(attachments)
    companion = _get_companion(user_id)

    if thread_id is not None:
        thread = runtime_db.get(RuntimeThread, thread_id)
        if thread is None or thread.user_id != user_id:
            raise ValueError(f"Thread {thread_id} not found for user {user_id}")
        if thread.status != "active":
            dek = await get_active_dek_async(user_id, "conversations")
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

    maybe_set_thread_title(runtime_db, thread, user_message)
    prev_thread_id = companion.thread_id
    companion.thread_id = thread.id
    if prev_thread_id != thread.id:
        companion.invalidate_history(thread_id=thread.id)

    # Use cached conversation history when available, otherwise load from DB.
    history = list(companion.ensure_history_loaded(runtime_db, thread_id=thread.id))

    prepared_attachments = _prepare_image_attachments(
        user_id=user_id,
        attachments=attachments,
        runtime_db=runtime_db,
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
        for offset, (context_message, cleaned_content) in enumerate(cleaned_context_messages):
            content_json = attach_serialized_pills(
                None,
                [_serialize_context_pill(pill) for pill in context_message.pills],
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
            pills=_build_user_message_document_pills(
                runtime_db,
                user_id=user_id,
                document_ids=document_ids,
            ),
        )
        conversation_turn_count = count_messages_by_role(runtime_db, thread.id, "user")
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
    except asyncio.CancelledError as exc:
        # Client disconnects cancel the request task; CancelledError is a
        # BaseException, so the Exception handler below never sees it.
        _fail_turn_setup(
            runtime_db,
            run=run,
            user_msg=user_msg,
            context_messages=persisted_context_messages,
            exc=exc,
            cancelled=True,
        )
        raise
    except Exception as exc:
        _fail_turn_setup(
            runtime_db,
            run=run,
            user_msg=user_msg,
            context_messages=persisted_context_messages,
            exc=exc,
        )
        raise
    return thread, run, user_msg, turn_ctx


async def _prepare_corefs_turn_context(
    user_message: str,
    user_id: int,
    db: Session,
    runtime_db: Session,
    *,
    authority_session: object,
    thread_id: int | None,
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None,
    source: str | None,
    attachments: Sequence[ChatRequestAttachment],
    document_ids: Sequence[int],
    context_messages: Sequence[ChatContextMessage],
    today_context: TodayContext | None,
) -> tuple[RuntimeThread, RuntimeRun, RuntimeMessage, _TurnContext]:
    from anima_server.services.corefs.conversation_authority import (
        get_active_canonical_thread,
        get_canonical_thread,
    )
    from anima_server.services.corefs.conversation_mutations import (
        append_canonical_message,
        create_canonical_thread,
        reactivate_canonical_thread,
    )

    _validate_image_attachment_inputs(attachments)
    if attachments:
        raise AttachmentValidationError(
            "CoreFS chat attachment writes are not enabled until the asset adapter is active."
        )
    view = (
        get_canonical_thread(session=authority_session, thread_id=thread_id)
        if thread_id is not None
        else get_active_canonical_thread(session=authority_session)
    )
    if view is None and thread_id is None:
        view = create_canonical_thread(session=authority_session)
    if view is None:
        raise ValueError(f"Thread {thread_id} not found for user {user_id}")
    if view.document.status != "active":
        view = reactivate_canonical_thread(
            session=authority_session,
            thread_id=thread_id if thread_id is not None else view.document.thread_id,
        )
        if view is None:
            raise ValueError(f"Thread {thread_id} not found for user {user_id}")
    legacy_thread_id = view.document.legacy_thread_id
    if isinstance(legacy_thread_id, bool) or not isinstance(legacy_thread_id, int):
        raise RuntimeError("Canonical thread has no compatible Runtime reference identity.")
    thread = ensure_runtime_thread_reference(
        runtime_db,
        user_id=user_id,
        thread_id=legacy_thread_id,
    )
    companion = _get_companion(user_id)
    previous_thread_id = companion.thread_id
    companion.thread_id = thread.id
    if previous_thread_id != thread.id:
        companion.invalidate_history(thread_id=thread.id)
    history = [StoredMessage(role=message.role, content=message.content) for message in view.messages]
    cleaned_context_messages = [
        (message, message.content.strip())
        for message in context_messages
        if message.content.strip()
    ]
    run = create_run(
        runtime_db,
        thread_id=thread.id,
        user_id=user_id,
        provider=settings.agent_provider,
        model=settings.agent_model,
        mode="streaming" if event_callback is not None else "blocking",
    )
    persisted_context_messages: list[RuntimeMessage] = []
    for context_message, cleaned_content in cleaned_context_messages:
        canonical = append_canonical_message(
            session=authority_session,
            thread_id=thread.id,
            role=context_message.role,
            content=cleaned_content,
        )
        reference = append_corefs_message_reference(
            runtime_db,
            thread=thread,
            message=canonical,
            run_id=None,
            source=context_message.source,
            transient_content_json=attach_serialized_pills(
                None,
                [_serialize_context_pill(pill) for pill in context_message.pills],
            ),
        )
        persisted_context_messages.append(reference)
        history.append(StoredMessage(role=canonical.role, content=canonical.content))
    user_content_json = attach_serialized_pills(
        None,
        _build_user_message_document_pills(
            runtime_db,
            user_id=user_id,
            document_ids=document_ids,
        ),
    )
    canonical_user = append_canonical_message(
        session=authority_session,
        thread_id=thread.id,
        role="user",
        content=user_message,
    )
    user_msg = append_corefs_message_reference(
        runtime_db,
        thread=thread,
        message=canonical_user,
        run_id=run.id,
        source=source,
        transient_content_json=user_content_json,
    )
    conversation_turn_count = sum(message.role == "user" for message in view.messages) + sum(
        message.role == "user" for message, _content in cleaned_context_messages
    ) + 1
    runtime_db.commit()
    turn_ctx = await _assemble_turn_context(
        user_message=user_message,
        user_id=user_id,
        db=db,
        runtime_db=runtime_db,
        thread=thread,
        companion=companion,
        history=history,
        conversation_turn_count=conversation_turn_count,
        prepared_attachments=(),
        document_ids=document_ids,
        persisted_context_messages=persisted_context_messages,
        today_context=today_context,
    )
    return thread, run, user_msg, turn_ctx


def _serialize_context_pill(pill: MessagePill) -> dict[str, object]:
    payload = pill.model_dump(exclude_none=True)
    payload.setdefault("ref", None)
    return payload


def _fail_turn_setup(
    runtime_db: Session,
    *,
    run: RuntimeRun,
    user_msg: RuntimeMessage,
    context_messages: Sequence[RuntimeMessage] = (),
    exc: BaseException,
    cancelled: bool = False,
) -> None:
    """Best-effort cleanup when a turn fails after the run and user message
    were committed (early-commit in turn preparation) but before the run
    reached a terminal state.

    Evicts the orphaned user message (and any context messages) from
    active context and marks the run failed (or cancelled, when
    *cancelled* is set — e.g. a client disconnect during setup), so the
    run does not stay "running" forever and the message does not replay
    as unanswered history on the next turn.

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
        _evict_failed_turn_messages(
            runtime_db,
            user_msg=user_msg,
            context_messages=context_messages,
        )
        if cancelled:
            cancel_run(runtime_db, run.id)
        else:
            mark_run_failed(runtime_db, run, str(exc))
        runtime_db.commit()
    except Exception:
        logger.exception("Failed to clean up run %s after turn-setup failure", run.id)
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
    #
    # The ops-only promotion runs CONCURRENTLY with hybrid retrieval below
    # (it uses its own sessions and touches soul blocks, not memory items)
    # and is awaited before the static blocks load — the two used to run
    # back-to-back on the TTFT critical path.
    soul_ops_task: asyncio.Task[Any] | None = None
    eligible_candidates = 0
    try:
        from anima_server.services.agent.candidate_ops import count_eligible_candidates
        from anima_server.services.agent.pending_ops import count_pending_ops
        from anima_server.services.agent.soul_writer import run_soul_writer

        pending = count_pending_ops(runtime_db, user_id=user_id)
        eligible_candidates = count_eligible_candidates(runtime_db, user_id=user_id)
        if pending > 0:
            soul_ops_task = asyncio.get_running_loop().create_task(
                run_soul_writer(user_id, ops_only=True)
            )
    except Exception:
        logger.debug("Pre-turn Soul Writer check failed for user %s", user_id, exc_info=True)

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
                # The item scores are the fused ranking scale (top renormalised
                # to 1.0); gate the absolute-confidence floor on the best hit's
                # raw cosine so a genuinely-irrelevant top match is rejected.
                confidence_score=search_result.max_cosine,
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
            plaintexts = search_result.plaintexts or {}
            for index, (item, score) in enumerate(filtered, start=1):
                # hybrid_search already decrypted every surviving item —
                # this loop used to be the third AEAD pass over the same
                # rows before first token.
                content = plaintexts.get(item.id) or df(
                    user_id, item.content, table="memory_items", field="content"
                )
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

    # Pending ops must be promoted before the static blocks load below;
    # the full candidate run (LLM-backed) only starts afterwards so it
    # can't steal the per-user soul-writer lock from the ops-only pass.
    if soul_ops_task is not None:
        try:
            await soul_ops_task
        except Exception:
            logger.debug(
                "Pre-turn ops-only Soul Writer failed for user %s",
                user_id,
                exc_info=True,
            )
    if eligible_candidates > 0:
        from anima_server.services.agent.soul_writer import run_soul_writer

        _track_background_task(run_soul_writer(user_id))

    effective_document_ids = _resolve_turn_document_ids(
        runtime_db,
        thread_id=thread.id,
        user_id=user_id,
        document_ids=document_ids,
    )
    document_context_block = _build_document_context_block(
        runtime_db,
        user_id=user_id,
        user_message=user_message,
        document_ids=effective_document_ids,
    )
    today_context_block = _build_today_context_block(today_context)

    if document_context_block is not None:
        document_turn_directive = _build_document_turn_directive(
            document_ids=effective_document_ids,
        )
        memory_blocks = tuple(
            block
            for block in (
                document_turn_directive,
                document_context_block,
                today_context_block,
            )
            if block is not None
        )
        document_source_pills = _build_document_pills(
            runtime_db,
            user_id=user_id,
            document_ids=effective_document_ids,
            kind="document_source",
        )
        recalled_image_source_pills = ()
    else:
        # Static identity blocks come from the companion cache (version-counter
        # invalidated); only the query-ranked and volatile blocks are rebuilt
        # per turn.  When an indexed PDF is selected and retrieved, these blocks
        # are intentionally excluded so ambiguous prompts like "what do you see"
        # stay grounded in the document instead of personal memories.
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
        if today_context_block is not None:
            memory_blocks = (*memory_blocks, today_context_block)
        document_source_pills = ()
        recalled_image_source_pills = (
            ()
            if prepared_attachments
            else _build_recalled_image_source_pills(
                runtime_db,
                user_id=user_id,
                query_embedding=query_embedding,
            )
        )

    # Feedback signals (best-effort) feed FUTURE turns (retrieval already ran
    # for this one) and the correction path decrypts up to 50 memory items, so
    # they run in the background — but the spawn now lives in the post-turn
    # hooks (Stage 4), which run only AFTER the turn's own rows are committed.
    # Spawning here (pre-invoke) raced the turn's message writes: the feedback
    # task opens its own runtime session and commits, and on a shared DB
    # connection that interleaved with the in-flight message INSERTs.

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
        has_document_context=document_context_block is not None,
        document_source_pills=document_source_pills,
        recalled_image_source_pills=recalled_image_source_pills,
    )


async def _process_feedback_signals_background(
    *,
    user_id: int,
    user_message: str,
    thread_id: int,
    soul_db_factory: Callable[..., Session],
    runtime_db_factory: Callable[..., Session],
) -> None:
    """Detect and apply feedback signals off the turn's critical path.

    Runs the synchronous decrypt-heavy work in a thread with its own
    sessions; results influence future turns, never the one in flight.
    """

    def _run() -> None:
        from anima_server.services.agent.feedback_signals import (
            apply_memory_correction,
            collect_feedback_signals,
            record_feedback_signals,
        )

        with soul_db_factory() as soul_db, runtime_db_factory() as bg_runtime_db:
            signals = collect_feedback_signals(
                user_id=user_id,
                user_message=user_message,
                thread_id=thread_id,
                runtime_db=bg_runtime_db,
            )
            if not signals:
                return
            record_feedback_signals(
                soul_db,
                user_id=user_id,
                signals=signals,
                runtime_db=bg_runtime_db,
            )
            # When a correction is detected, fix the underlying memory
            if any(s.signal_type == "correction" for s in signals):
                apply_memory_correction(
                    soul_db,
                    user_id=user_id,
                    user_message=user_message,
                    thread_id=thread_id,
                    runtime_db=bg_runtime_db,
                )
            soul_db.commit()
            bg_runtime_db.commit()

    try:
        await asyncio.to_thread(_run)
    except Exception:
        logger.warning(
            "Background feedback signal processing failed for user %s",
            user_id,
            exc_info=True,
        )


def _resolve_turn_document_ids(
    runtime_db: Session,
    *,
    thread_id: int,
    user_id: int,
    document_ids: Sequence[int],
) -> list[int]:
    explicit_ids = _dedupe_positive_ids(document_ids)
    if explicit_ids:
        return explicit_ids
    return _recent_thread_document_ids(
        runtime_db,
        thread_id=thread_id,
        user_id=user_id,
    )


def _recent_thread_document_ids(
    runtime_db: Session,
    *,
    thread_id: int,
    user_id: int,
    limit: int = 12,
) -> list[int]:
    messages = runtime_db.execute(
        select(RuntimeMessage)
        .where(RuntimeMessage.thread_id == thread_id)
        .where(RuntimeMessage.user_id == user_id)
        .order_by(RuntimeMessage.sequence_id.desc(), RuntimeMessage.id.desc())
        .limit(limit)
    ).scalars()
    document_ids: list[int] = []
    seen: set[int] = set()
    for message in messages:
        if message.is_internal:
            continue
        message_document_ids: list[int] = []
        for pill in extract_stored_pills(message.content_json):
            if pill.get("kind") not in _DOCUMENT_PILL_KINDS:
                continue
            try:
                document_id = int(pill.get("ref"))
            except (TypeError, ValueError):
                continue
            if document_id <= 0 or document_id in seen:
                continue
            if (
                get_document_for_user(
                    runtime_db,
                    user_id=user_id,
                    document_id=document_id,
                )
                is None
            ):
                continue
            seen.add(document_id)
            message_document_ids.append(document_id)
        if message_document_ids:
            document_ids.extend(message_document_ids)
            break
    return document_ids


def _build_document_pills(
    runtime_db: Session,
    *,
    user_id: int,
    document_ids: Sequence[int],
    kind: str,
) -> tuple[dict[str, object], ...]:
    pills: list[dict[str, object]] = []
    for document_id in _dedupe_positive_ids(document_ids):
        document = get_document_for_user(
            runtime_db,
            user_id=user_id,
            document_id=document_id,
        )
        if document is None:
            continue
        pills.append(
            {
                "kind": kind,
                "label": _truncate_pill_label(document.filename),
                "ref": document.id,
            }
        )
    return tuple(pills)


def _build_user_message_document_pills(
    runtime_db: Session,
    *,
    user_id: int,
    document_ids: Sequence[int],
) -> tuple[dict[str, object], ...]:
    return _build_document_pills(
        runtime_db,
        user_id=user_id,
        document_ids=document_ids,
        kind="document_attachment",
    )


def _build_recalled_image_source_pills(
    runtime_db: Session,
    *,
    user_id: int,
    query_embedding: Sequence[float] | None,
    limit: int = _MAX_RECALLED_IMAGE_SOURCE_PILLS,
) -> tuple[dict[str, object], ...]:
    if not query_embedding or limit <= 0:
        return ()

    try:
        from anima_server.services.images.rag import (
            search_image_annotations_by_embedding,
        )

        results = search_image_annotations_by_embedding(
            runtime_db,
            user_id=user_id,
            query_embedding=query_embedding,
            limit=limit,
        )
    except Exception:
        logger.debug("Image source pill recall failed for user %s", user_id, exc_info=True)
        return ()

    pills: list[dict[str, object]] = []
    seen_assets: set[int] = set()
    for result in results:
        # Any recalled image annotation should surface a clickable source
        # pill, not just vision/OCR captions.  The indexer always writes
        # upload_context/metadata annotations, and where vision/OCR is
        # disabled — or upload context outscores a caption — those are what
        # the prompt memory block recalls, so restricting to
        # {vision_caption, ocr_text} dropped the pill for a genuinely used
        # image (the response then had no clickable image_source).  Dedup by
        # asset keeps the top-scoring annotation per image.
        if result.image_asset_id in seen_assets or not result.attachment_url:
            continue
        seen_assets.add(result.image_asset_id)
        pill: dict[str, object] = {
            "kind": "image_source",
            "label": _truncate_pill_label(result.filename or f"image-{result.image_asset_id}"),
            "ref": f"image:{result.image_asset_id}",
            "assetId": result.image_asset_id,
            "mimeType": result.mime_type,
            "url": result.attachment_url,
        }
        if result.source_message_id is not None:
            pill["messageId"] = result.source_message_id
        if result.source_thread_id is not None:
            pill["threadId"] = result.source_thread_id
        if result.attachment_id:
            pill["attachmentId"] = result.attachment_id
        if len(result.related_sources) > 1:
            pill["relatedCount"] = len(result.related_sources)
        pills.append(pill)

    return tuple(pills)


def _build_assistant_source_pills(
    turn_ctx: _TurnContext,
    *,
    source_message: RuntimeMessage | None = None,
) -> tuple[dict[str, object], ...]:
    pills: list[dict[str, object]] = []
    if turn_ctx.has_document_context:
        if turn_ctx.document_source_pills:
            pills.extend(turn_ctx.document_source_pills)
        else:
            pills.append({"kind": "document_source", "label": "CITED DOCS", "ref": None})
    for attachment in turn_ctx.attachments:
        pill: dict[str, object] = {
            "kind": "image_source",
            "label": _truncate_pill_label(attachment.filename or "Image"),
            "ref": attachment.id,
            "mimeType": attachment.mime_type,
        }
        if attachment.asset_id is not None:
            pill["assetId"] = attachment.asset_id
        if source_message is not None:
            pill["url"] = f"/api/chat/messages/{source_message.id}/attachments/{attachment.id}"
            pill["messageId"] = source_message.id
            pill["threadId"] = source_message.thread_id
            pill["attachmentId"] = attachment.id
        pills.append(pill)
    if not turn_ctx.attachments:
        pills.extend(turn_ctx.recalled_image_source_pills)
    return tuple(pills)


def _truncate_pill_label(label: str, *, limit: int = 64) -> str:
    cleaned = " ".join(label.strip().split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def _build_document_context_block(
    runtime_db: Session,
    *,
    user_id: int,
    user_message: str,
    document_ids: Sequence[int],
) -> MemoryBlock | None:
    cleaned_document_ids = _dedupe_positive_ids(document_ids)
    if not cleaned_document_ids:
        return None
    # Stale, deleted, or not-owned ids must not push the turn into document
    # mode (which suppresses personal memory): validate ownership first and
    # behave like no selection when nothing valid remains. Lookup failures
    # fail open so a transient DB error does not silently drop a valid
    # selection.
    try:
        owned_document_ids = [
            document_id
            for document_id in cleaned_document_ids
            if get_document_for_user(
                runtime_db,
                user_id=user_id,
                document_id=document_id,
            )
            is not None
        ]
    except Exception:
        logger.debug(
            "Selected document ownership check failed for user %s",
            user_id,
            exc_info=True,
        )
    else:
        if not owned_document_ids:
            return None
        cleaned_document_ids = owned_document_ids
    query = user_message.strip() or _default_document_only_user_message(cleaned_document_ids)

    # Full-document context: when the combined text of every selected
    # document fits the window-scaled budget, inject the whole documents
    # instead of retrieved chunks (matches "paste the doc in" quality for
    # small selections). All-or-nothing: any overflow falls back to the
    # retrieval path below so a turn never mixes full and retrieved
    # evidence. Fails open (falls back to retrieval) on any load error.
    full_document_texts: list[tuple[RuntimeDocument, str]] | None = None
    if settings.document_full_context == "auto":
        try:
            full_document_texts = _full_document_texts(
                runtime_db,
                user_id=user_id,
                document_ids=cleaned_document_ids,
            )
        except Exception:
            logger.debug(
                "Full-document context load failed for user %s documents %s",
                user_id,
                cleaned_document_ids,
                exc_info=True,
            )
            full_document_texts = None

    if full_document_texts is not None:
        full_document_block = _build_full_document_memory_block(
            runtime_db,
            user_id=user_id,
            document_texts=full_document_texts,
        )
        # The final fit decision is made on the ASSEMBLED value: the
        # preamble, per-document markers, and knowledge-hit entries add
        # real characters on top of the document text, and the planner's
        # per-block cap is this same budget — so only a block that fits
        # fully assembled is guaranteed to survive prompt assembly
        # untruncated. Over-budget assembly falls back to retrieval
        # (all-or-nothing: no mixed full+retrieved evidence).
        if len(full_document_block.value) <= resolve_document_context_budget_chars():
            return full_document_block

    try:
        results = search_document_chunks(
            runtime_db,
            user_id,
            query,
            document_ids=cleaned_document_ids,
            limit=settings.document_context_chunk_limit,
        )
    except Exception:
        logger.debug(
            "Document retrieval failed for user %s documents %s",
            user_id,
            cleaned_document_ids,
            exc_info=True,
        )
        # Fall through with no hits: the primer below still names the
        # selected documents and the tools, so the turn can recover
        # through get_document_outline / read_document_section.
        results = []

    try:
        knowledge_hits = _document_knowledge_hits(
            runtime_db,
            user_id=user_id,
            document_ids=cleaned_document_ids,
            document_chunk_ids=[result.chunk_id for result in results],
            limit=8,
        )
    except Exception:
        logger.debug(
            "Document knowledge retrieval failed for user %s documents %s",
            user_id,
            cleaned_document_ids,
            exc_info=True,
        )
        knowledge_hits = []

    # Even with zero retrieval hits (embedding outage, query missing the
    # index) the primer must still name the selected documents and the
    # document tools — the block is the model's only signal that documents
    # were selected for this turn.
    lines = [
        "Selected document context from indexed PDFs. Use this only when it is relevant.",
    ]
    if not results and not knowledge_hits:
        lines.append("")
        lines.append(
            "No excerpts were retrieved for this query. Use the document tools "
            "below to investigate the selected documents directly."
        )
    if knowledge_hits:
        lines.append("")
        lines.append("Compiled knowledge from selected PDFs:")
        for index, hit in enumerate(knowledge_hits, start=1):
            lines.append(
                f"[K{index}] {hit.title} "
                f"({hit.concept_type}, concept {hit.concept_id}, selected source)"
            )
            lines.append(_truncate_document_chunk(hit.summary, limit=700))

    raw_results = _raw_document_results_without_compiled_coverage(
        runtime_db,
        user_id=user_id,
        results=results,
        knowledge_hits=knowledge_hits,
    )
    if raw_results:
        lines.append("")
        lines.append("Raw evidence excerpts from selected PDFs:")
        for index, result in enumerate(raw_results, start=1):
            location = _format_document_location(result)
            section = f", section {result.section_title}" if result.section_title else ""
            lines.append(
                f"[{index}] {result.filename}{location}{section} "
                f"(document {result.document_id}, chunk {result.chunk_id}, relevance {result.similarity:.2f})"
            )
            lines.append(
                _truncate_document_chunk(
                    result.content,
                    limit=settings.document_context_chunk_char_cap,
                )
            )

    selected_lines = []
    try:
        for document_id in cleaned_document_ids:
            document = get_document_for_user(
                runtime_db,
                user_id=user_id,
                document_id=document_id,
            )
            if document is not None:
                selected_lines.append(f"- doc:{document.id} {document.filename}")
    except Exception:
        logger.debug("Selected document listing failed for user %s", user_id, exc_info=True)
        selected_lines = []
    if selected_lines:
        lines.append("")
        lines.append("Selected documents:")
        lines.extend(selected_lines)
    lines.append("")
    lines.append(
        "These excerpts are only an orientation sample. Use the search_documents, "
        "get_document_outline, and read_document_section tools to investigate the "
        "documents beyond what is shown here."
    )

    return MemoryBlock(
        label="document_context",
        value="\n".join(lines),
        description=(
            "Compiled source knowledge and query-relevant excerpts from PDFs the user explicitly "
            "selected for this chat turn. Ground answers in these snippets when they apply; "
            "do not treat them as long-term memory."
        ),
    )


def _full_document_texts(
    runtime_db: Session,
    *,
    user_id: int,
    document_ids: Sequence[int],
) -> list[tuple[RuntimeDocument, str]] | None:
    """Load each selected document's full text in chunk order.

    Returns ``None`` (all-or-nothing) when the combined length of every
    selected document exceeds the full-document budget: mixed full and
    retrieved evidence in the same turn is confusing, so any overflow sends
    the whole turn back through the retrieval path. This text-only check is
    a cheap pre-filter that can only reject; the caller makes the final fit
    decision on the fully ASSEMBLED block value against the same budget
    (``prompt_budget.resolve_document_context_budget_chars``), so a block
    that ships is never re-truncated by prompt assembly.
    """
    budget_chars = resolve_document_context_budget_chars()

    # Cheap admission check: reject an oversized selection via a SQL length
    # aggregate BEFORE loading any chunk body. `SUM(content_char_count)` is
    # a strict lower bound of the assembled text length (assembly still adds
    # "\n\n" separators between chunks and per-document markers on top), so
    # rejecting when the aggregate alone exceeds budget is always correct.
    # This only rejects — it never admits on its own; a selection that
    # passes still runs the existing per-document load/measure loop below
    # unchanged, and the caller still makes the final fit decision on the
    # fully assembled block value. Works on both Postgres (prod) and sqlite
    # (tests): LENGTH/SUM are standard on both backends. Fails open (falls
    # through to the existing path) on any query error rather than crash.
    try:
        chunk_length_sum = runtime_db.scalar(
            select(func.sum(RuntimeDocumentChunk.content_char_count)).where(
                RuntimeDocumentChunk.document_id.in_(document_ids),
                RuntimeDocumentChunk.user_id == user_id,
            )
        )
    except Exception:
        logger.debug(
            "Full-document length aggregate failed for user %s documents %s; "
            "falling back to per-document loading",
            user_id,
            document_ids,
            exc_info=True,
        )
    else:
        if chunk_length_sum is not None and chunk_length_sum > budget_chars:
            return None

    documents: list[tuple[RuntimeDocument, str]] = []
    total_chars = 0
    for document_id in document_ids:
        document = get_document_for_user(
            runtime_db,
            user_id=user_id,
            document_id=document_id,
        )
        if document is None:
            continue
        chunks = list_document_chunks(runtime_db, document_id=document_id)
        if not chunks:
            # No chunked text to inject (document not yet processed through
            # the chunking pipeline) — fall back to retrieval rather than
            # silently injecting an empty document.
            return None
        text = "\n\n".join(chunk.content_text for chunk in chunks)
        total_chars += len(text)
        if total_chars > budget_chars:
            return None
        documents.append((document, text))
    if not documents:
        # Every selected id was dropped by this loop's own ownership/existence
        # check (e.g. the outer caller's ownership check failed open and kept
        # stale ids). An empty list is indistinguishable from "no selection"
        # to callers gating on `is not None`, which would otherwise ship a
        # "Full text of the selected documents" block with zero document
        # text. Signal the same retrieval fallback as any other rejection.
        return None
    return documents


def _build_full_document_memory_block(
    runtime_db: Session,
    *,
    user_id: int,
    document_texts: Sequence[tuple[RuntimeDocument, str]],
) -> MemoryBlock:
    try:
        knowledge_hits = _document_knowledge_hits(
            runtime_db,
            user_id=user_id,
            document_ids=[document.id for document, _ in document_texts],
            document_chunk_ids=[],
            limit=8,
        )
    except Exception:
        logger.debug(
            "Document knowledge retrieval failed for user %s during full-document context",
            user_id,
            exc_info=True,
        )
        knowledge_hits = []

    lines = [
        "Selected document context from indexed PDFs. Use this only when it is relevant.",
    ]
    if knowledge_hits:
        lines.append("")
        lines.append("Compiled knowledge from selected PDFs:")
        for index, hit in enumerate(knowledge_hits, start=1):
            lines.append(
                f"[K{index}] {hit.title} "
                f"({hit.concept_type}, concept {hit.concept_id}, selected source)"
            )
            lines.append(_truncate_document_chunk(hit.summary, limit=700))

    lines.append("")
    lines.append(
        "Full text of the selected documents (small enough to include in full "
        "instead of retrieved excerpts):"
    )
    for document, text in document_texts:
        lines.append("")
        lines.append(f"--- {document.filename} [doc:{document.id}] (complete document) ---")
        lines.append(text)

    return MemoryBlock(
        label="document_context",
        value="\n".join(lines),
        description=(
            "Complete text of PDFs the user explicitly selected for this chat turn, "
            "injected in full because the selection fits the context budget. Ground "
            "answers in this text when it applies; do not treat it as long-term memory."
        ),
    )


def _raw_document_results_without_compiled_coverage(
    runtime_db: Session,
    *,
    user_id: int,
    results: Sequence[DocumentRagResult],
    knowledge_hits: Sequence[KnowledgeConceptHit],
) -> list[DocumentRagResult]:
    if not results:
        return []
    if not knowledge_hits:
        return list(results)
    result_chunk_ids = {result.chunk_id for result in results if result.chunk_id > 0}
    concept_ids = _dedupe_positive_ids([hit.concept_id for hit in knowledge_hits])
    if not result_chunk_ids or not concept_ids:
        return list(results)
    try:
        covered_chunk_ids = _compiled_document_chunk_ids(
            runtime_db,
            user_id=user_id,
            concept_ids=concept_ids,
            document_chunk_ids=result_chunk_ids,
        )
    except Exception:
        logger.debug(
            "Document compiled chunk coverage lookup failed for user %s",
            user_id,
            exc_info=True,
        )
        return list(results)
    return [result for result in results if result.chunk_id not in covered_chunk_ids]


def _compiled_document_chunk_ids(
    runtime_db: Session,
    *,
    user_id: int,
    concept_ids: Sequence[int],
    document_chunk_ids: set[int],
) -> set[int]:
    if not concept_ids or not document_chunk_ids:
        return set()
    rows = runtime_db.execute(
        select(RuntimeSourceSpan.locator_json)
        .join(
            RuntimeKnowledgeConceptSource,
            (RuntimeKnowledgeConceptSource.span_id == RuntimeSourceSpan.id)
            & (RuntimeKnowledgeConceptSource.user_id == RuntimeSourceSpan.user_id),
        )
        .where(
            RuntimeKnowledgeConceptSource.user_id == user_id,
            RuntimeKnowledgeConceptSource.concept_id.in_(list(concept_ids)),
        )
    ).all()
    covered_chunk_ids: set[int] = set()
    for (locator,) in rows:
        if not isinstance(locator, dict):
            continue
        chunk_id = locator.get("runtime_document_chunk_id")
        if isinstance(chunk_id, int) and chunk_id in document_chunk_ids:
            covered_chunk_ids.add(chunk_id)
    return covered_chunk_ids


def _document_knowledge_hits(
    runtime_db: Session,
    *,
    user_id: int,
    document_ids: Sequence[int],
    document_chunk_ids: Sequence[int] | None = None,
    limit: int = 8,
) -> list[KnowledgeConceptHit]:
    if limit <= 0:
        return []
    source_ids = _document_source_ids(
        runtime_db,
        user_id=user_id,
        document_ids=document_ids,
    )
    if not source_ids:
        return []
    concepts = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeConcept)
            .where(
                RuntimeKnowledgeConcept.user_id == user_id,
                RuntimeKnowledgeConcept.status == "active",
                RuntimeKnowledgeConcept.metadata_json["compiled_from_source_id"]
                .as_integer()
                .in_(source_ids),
            )
            .order_by(
                RuntimeKnowledgeConcept.updated_at.desc(),
                RuntimeKnowledgeConcept.id.desc(),
            )
        ).all()
    )
    query_matched = _query_matched_document_concepts(
        runtime_db,
        user_id=user_id,
        concepts=concepts,
        document_chunk_ids=document_chunk_ids,
    )
    if document_chunk_ids:
        concepts = query_matched
    elif not query_matched:
        concepts.sort(key=lambda concept: (concept.concept_type != "source_summary", concept.id))
    else:
        concepts = query_matched
    return [
        KnowledgeConceptHit(
            concept_id=concept.id,
            title=concept.title,
            slug=concept.slug,
            concept_type=concept.concept_type,
            summary=_document_knowledge_summary(concept),
            score=1.0 if concept.concept_type == "source_summary" else 0.8,
        )
        for concept in concepts[:limit]
    ]


def _query_matched_document_concepts(
    runtime_db: Session,
    *,
    user_id: int,
    concepts: Sequence[RuntimeKnowledgeConcept],
    document_chunk_ids: Sequence[int] | None,
) -> list[RuntimeKnowledgeConcept]:
    chunk_ids = set(_dedupe_positive_ids(document_chunk_ids or ()))
    if not chunk_ids:
        return []
    concepts_by_id = {
        concept.id: concept for concept in concepts if concept.concept_type != "source_summary"
    }
    if not concepts_by_id:
        return []
    matched_ids: set[int] = set()
    rows = runtime_db.execute(
        select(
            RuntimeKnowledgeConceptSource.concept_id,
            RuntimeSourceSpan.locator_json,
        )
        .join(
            RuntimeSourceSpan,
            (RuntimeSourceSpan.id == RuntimeKnowledgeConceptSource.span_id)
            & (RuntimeSourceSpan.user_id == RuntimeKnowledgeConceptSource.user_id),
        )
        .where(
            RuntimeKnowledgeConceptSource.user_id == user_id,
            RuntimeKnowledgeConceptSource.concept_id.in_(list(concepts_by_id)),
        )
    ).all()
    for concept_id, locator in rows:
        if not isinstance(locator, dict):
            continue
        chunk_id = locator.get("runtime_document_chunk_id")
        if isinstance(chunk_id, int) and chunk_id in chunk_ids:
            matched_ids.add(concept_id)
    return [
        concept
        for concept in concepts
        if concept.id in matched_ids and concept.concept_type != "source_summary"
    ]


def _document_source_ids(
    runtime_db: Session,
    *,
    user_id: int,
    document_ids: Sequence[int],
) -> list[int]:
    source_uris = [f"runtime-document://{document_id}" for document_id in document_ids]
    if not source_uris:
        return []
    source_uris = [
        runtime_private_exact_lookup_value(
            runtime_db,
            owner_id=user_id,
            value=source_uri,
            namespace="runtime_source.source_uri",
        )
        for source_uri in source_uris
    ]
    return list(
        runtime_db.scalars(
            select(RuntimeSource.id).where(
                RuntimeSource.user_id == user_id,
                RuntimeSource.kind == "document",
                RuntimeSource.source_uri.in_(source_uris),
            )
        ).all()
    )


def _document_knowledge_summary(concept: RuntimeKnowledgeConcept) -> str:
    return concept.body_markdown or concept.description or concept.title


def _build_document_turn_directive(
    *,
    document_ids: Sequence[int],
) -> MemoryBlock | None:
    document_count = len(_dedupe_positive_ids(document_ids))
    if document_count <= 0:
        return None

    noun = "selected PDF" if document_count == 1 else "selected PDFs"
    return MemoryBlock(
        label="user_directive",
        value=(
            f"For this turn, the user's question is primarily about the {noun}. "
            "Answer from the selected document context first when it is relevant. "
            "If the wording is ambiguous, such as 'what do you see' or 'what is this', "
            "interpret it as asking what is in the selected PDF. "
            "Do not substitute personal memory, relationship context, or stylistic inference "
            "when the selected document can answer the question. The injected excerpts are only "
            "an orientation sample: when they are insufficient, investigate with the "
            "search_documents, get_document_outline, and read_document_section tools before "
            "concluding anything is missing. If the document still cannot answer, say what is "
            "missing plainly."
        ),
        description=(
            "Per-turn grounding rule for explicit PDF selections. Selected document evidence "
            "takes precedence over general memory when answering this message."
        ),
        read_only=True,
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


def _truncate_document_chunk(content: str, *, limit: int) -> str:
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
    # Conservatively estimate memory block and history tokens from characters.
    block_chars = sum(len(b.value) for b in memory_blocks)
    history_chars = sum(len(m.content or "") for m in history)
    estimated_tokens = estimate_char_tokens(block_chars + history_chars)

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

    # Note: this block has no dedicated budget policy (default tier 3), so
    # on a near-budget document turn — where a window-scaled
    # document_context block may consume most of the total block budget —
    # the planner can now drop it with total_budget_exhausted, whereas the
    # old static 4000-char document cap left room for it. Acceptable: the
    # warning is advisory, and genuine context overflow is still handled by
    # _proactive_compact_if_needed.
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
    event_callback: Callable[[AgentStreamEvent], Awaitable[None]] | None = None,
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
            prepared_action_schemas = prepare_action_tool_schemas(extra_tool_schemas)
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
            health_emit(
                "llm",
                "compaction",
                "info",
                user_id=user_id,
                data={
                    "compacted_messages": compacted.compacted_message_count,
                },
            )

            # Post-compaction: promote pending candidates
            try:
                from anima_server.services.agent.soul_writer import run_soul_writer

                await run_soul_writer(user_id)
            except Exception:
                logger.debug("Post-emergency-compaction Soul Writer failed", exc_info=True)

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
        _evict_failed_turn_messages(
            runtime_db,
            user_msg=user_msg,
            context_messages=turn_ctx.context_messages,
        )
        mark_run_failed(runtime_db, run, str(exc))
        runtime_db.commit()
        raise
    finally:
        _capture_document_tool_citations(turn_ctx)
        clear_tool_context()


def _capture_document_tool_citations(turn_ctx: _TurnContext) -> None:
    """Fold documents cited by the document tools into the turn's source pills.

    Runs before the tool context is cleared so tool-driven citations keep the
    document_source provenance UX working even when the turn started without
    an injected document context block.
    """
    ctx = peek_tool_context()
    if ctx is None or not ctx.document_tool_citations:
        return
    existing_refs = {pill.get("ref") for pill in turn_ctx.document_source_pills}
    new_pills = tuple(
        {
            "kind": "document_source",
            "label": _truncate_pill_label(filename),
            "ref": document_id,
        }
        for document_id, filename in ctx.document_tool_citations.items()
        if document_id not in existing_refs
    )
    if new_pills:
        turn_ctx.document_source_pills = (*turn_ctx.document_source_pills, *new_pills)
        turn_ctx.has_document_context = True


async def _proactive_compact_if_needed(
    runtime_db: Session,
    *,
    thread: RuntimeThread,
    run: RuntimeRun,
    turn_ctx: _TurnContext,
    user_id: int,
) -> _TurnContext:
    """Compact oversized legacy Runtime history before the first LLM call."""
    from anima_server.services.corefs.conversation_authority import (
        active_conversation_authority_session,
    )

    if active_conversation_authority_session(user_id) is not None:
        return turn_ctx
    block_chars = sum(len(b.value) for b in turn_ctx.memory_blocks)
    history_chars = sum(len(m.content or "") for m in turn_ctx.history)
    estimated_tokens = estimate_char_tokens(block_chars + history_chars)

    threshold = int(resolve_context_budget_tokens() * settings.agent_compaction_trigger_ratio)
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
        keep_last_messages=max(1, settings.agent_compaction_keep_last_messages),
        reserved_prompt_tokens=estimate_char_tokens(block_chars),
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
    return exc.context.step_index == 0 and exc.progression < StepProgression.TOOLS_STARTED


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
    conversation_turn_count = count_messages_by_role(runtime_db, thread.id, "user")
    return _TurnContext(
        history=history,
        conversation_turn_count=conversation_turn_count,
        memory_blocks=turn_ctx.memory_blocks,
        attachments=turn_ctx.attachments,
        context_messages=turn_ctx.context_messages,
        retrieval=turn_ctx.retrieval,
        has_document_context=turn_ctx.has_document_context,
        document_source_pills=turn_ctx.document_source_pills,
    )


def _memory_item_uri(memory_item_id: int) -> str:
    return f"memory://items/{memory_item_id}"


def _index_image_attachments_inline(
    runtime_db: Session,
    *,
    user_id: int,
    user_message: str,
    attachments: Sequence[StoredAttachment],
) -> None:
    image_asset_ids = [
        attachment.asset_id for attachment in attachments if attachment.asset_id is not None
    ]
    if not image_asset_ids:
        return
    try:
        from anima_server.services.images.indexing import index_image_attachments_for_message

        with runtime_db.begin_nested():
            index_image_attachments_for_message(
                runtime_db,
                user_id=user_id,
                image_asset_ids=image_asset_ids,
                upload_context=user_message,
            )
    except Exception:
        logger.debug("Inline image annotation indexing failed", exc_info=True)


def _index_run_user_image_attachments_inline(
    runtime_db: Session,
    *,
    user_id: int,
    run: RuntimeRun,
) -> None:
    user_message = runtime_db.scalar(
        select(RuntimeMessage)
        .where(
            RuntimeMessage.run_id == run.id,
            RuntimeMessage.user_id == user_id,
            RuntimeMessage.role == "user",
        )
        .order_by(RuntimeMessage.sequence_id, RuntimeMessage.id)
        .limit(1)
    )
    if user_message is None:
        return

    _index_image_attachments_inline(
        runtime_db,
        user_id=user_id,
        user_message=user_message.content_text or "",
        attachments=deserialize_stored_attachments(user_message.content_json),
    )


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
    _evict_failed_turn_messages(
        runtime_db,
        user_msg=user_msg,
        context_messages=context_messages,
    )

    detail = f"step {err.context.step_index} failed at {stage.name}: {err.cause}"
    mark_run_failed(runtime_db, run, detail)
    runtime_db.commit()


def _evict_failed_turn_messages(
    runtime_db: Session,
    *,
    user_msg: RuntimeMessage,
    context_messages: Sequence[RuntimeMessage] = (),
) -> None:
    user_msg.is_in_context = False
    runtime_db.add(user_msg)
    _remove_failed_turn_image_links(runtime_db, user_msg=user_msg)
    for context_message in context_messages:
        context_message.is_in_context = False
        runtime_db.add(context_message)


def _remove_failed_turn_image_links(
    runtime_db: Session,
    *,
    user_msg: RuntimeMessage,
) -> None:
    link_rows = list(
        runtime_db.execute(
            select(
                RuntimeImageMessageLink.attachment_id,
                RuntimeImageMessageLink.image_asset_id,
            ).where(
                RuntimeImageMessageLink.user_id == user_msg.user_id,
                RuntimeImageMessageLink.message_id == user_msg.id,
            )
        ).all()
    )
    image_asset_ids = {image_asset_id for _attachment_id, image_asset_id in link_rows}
    if not image_asset_ids:
        return

    _remove_failed_turn_attachment_metadata(
        runtime_db,
        user_msg,
        attachment_ids={attachment_id for attachment_id, _image_asset_id in link_rows},
        image_asset_ids=image_asset_ids,
    )
    runtime_db.execute(
        delete(RuntimeImageMessageLink).where(
            RuntimeImageMessageLink.user_id == user_msg.user_id,
            RuntimeImageMessageLink.message_id == user_msg.id,
        )
    )
    runtime_db.flush()

    from anima_server.services.images.deletion import _delete_orphaned_transient_asset

    for image_asset_id in image_asset_ids:
        _delete_orphaned_transient_asset(
            runtime_db,
            user_id=user_msg.user_id,
            image_asset_id=image_asset_id,
        )


def _remove_failed_turn_attachment_metadata(
    runtime_db: Session,
    user_msg: RuntimeMessage,
    *,
    attachment_ids: set[str],
    image_asset_ids: set[int],
) -> None:
    content_json = user_msg.content_json
    if not isinstance(content_json, dict):
        return
    raw_attachments = content_json.get(ATTACHMENTS_CONTENT_KEY)
    if not isinstance(raw_attachments, list):
        return

    filtered_attachments = [
        attachment
        for attachment in raw_attachments
        if not _is_failed_turn_attachment(
            attachment,
            attachment_ids=attachment_ids,
            image_asset_ids=image_asset_ids,
        )
    ]
    if len(filtered_attachments) == len(raw_attachments):
        return

    next_content_json = dict(content_json)
    if filtered_attachments:
        next_content_json[ATTACHMENTS_CONTENT_KEY] = filtered_attachments
    else:
        next_content_json.pop(ATTACHMENTS_CONTENT_KEY, None)
    reseal_runtime_message(
        runtime_db,
        user_msg,
        content_json=next_content_json or None,
    )


def _is_failed_turn_attachment(
    attachment: object,
    *,
    attachment_ids: set[str],
    image_asset_ids: set[int],
) -> bool:
    if not isinstance(attachment, dict):
        return False
    attachment_id = attachment.get("id")
    if isinstance(attachment_id, str) and attachment_id in attachment_ids:
        return True
    asset_id = attachment.get("assetId")
    return isinstance(asset_id, int) and asset_id in image_asset_ids


def _persist_approval_checkpoint(
    runtime_db: Session,
    *,
    thread: RuntimeThread,
    run: RuntimeRun,
    result: AgentResult,
    assistant_pills: tuple[dict[str, object], ...] = (),
) -> ToolCall | None:
    """Persist the agent result plus a role='approval' checkpoint message.

    Called when the runtime stops with ``AWAITING_APPROVAL``.  Persists
    the step traces (assistant message + tool-error result) and then
    adds the approval checkpoint message referencing the pending tool call.

    Returns the pending ``ToolCall`` so the caller can emit the
    ``approval_pending`` streaming event, or ``None`` if the tool call
    could not be reconstructed (run is marked failed in that case).
    """
    from anima_server.services.corefs.conversation_authority import (
        active_conversation_authority_session,
    )

    if active_conversation_authority_session(int(thread.user_id)) is not None:
        _persist_corefs_step_traces(runtime_db, thread=thread, run=run, result=result)
    else:
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
            assistant_pills=assistant_pills,
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
            runtime_db, run, "Could not reconstruct pending tool call for approval checkpoint"
        )
        runtime_db.commit()
        return None

    seq_id = reserve_message_sequences(runtime_db, thread_id=thread.id, count=1)
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
    assistant_pills: tuple[dict[str, object], ...] = (),
) -> None:
    """Stage 3: Write result to DB; schedule compaction in the background.

    Compaction (LLM summarization with a text-based fallback) runs as a
    background task so the client's ``done`` event is not delayed by a
    full non-streaming LLM call.
    """
    from anima_server.services.corefs.conversation_authority import (
        active_conversation_authority_session,
    )

    authority_session = active_conversation_authority_session(int(run.user_id))
    if authority_session is not None:
        await _persist_corefs_turn_result(
            runtime_db,
            authority_session=authority_session,
            thread=thread,
            run=run,
            result=result,
        )
        return

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
        assistant_pills=assistant_pills,
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


async def _persist_corefs_turn_result(
    runtime_db: Session,
    *,
    authority_session: object,
    thread: RuntimeThread,
    run: RuntimeRun,
    result: AgentResult,
) -> None:
    from anima_server.services.corefs.conversation_mutations import append_canonical_message

    last_step_id = _persist_corefs_step_traces(
        runtime_db,
        thread=thread,
        run=run,
        result=result,
    )
    if result.response:
        canonical = append_canonical_message(
            session=authority_session,
            thread_id=thread.id,
            role="assistant",
            content=result.response,
        )
        append_corefs_message_reference(
            runtime_db,
            thread=thread,
            message=canonical,
            run_id=run.id,
            step_id=last_step_id,
        )
    finalize_run(runtime_db, run=run, result=result)
    runtime_db.commit()


def _persist_corefs_step_traces(
    runtime_db: Session,
    *,
    thread: RuntimeThread,
    run: RuntimeRun,
    result: AgentResult,
) -> int | None:
    last_step_id: int | None = None
    prior_step_index = runtime_db.scalar(
        select(func.max(RuntimeStep.step_index)).where(RuntimeStep.run_id == run.id)
    )
    next_step_index = int(prior_step_index) + 1 if prior_step_index is not None else 0
    for trace_index, trace in enumerate(result.step_traces):
        step = create_step(
            runtime_db,
            thread_id=thread.id,
            run_id=run.id,
            trace=replace(trace, step_index=next_step_index + trace_index),
            prompt_budget=result.prompt_budget if trace_index == 0 else None,
        )
        last_step_id = int(step.id)
    return last_step_id


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
                        int(
                            resolve_context_budget_tokens()
                            * settings.agent_compaction_trigger_ratio
                        ),
                    ),
                    keep_last_messages=max(1, settings.agent_compaction_keep_last_messages),
                    reserved_prompt_tokens=reserved_prompt_tokens,
                )

                # Try LLM-powered compaction first (best-effort)
                compaction_result = None
                try:
                    compaction_result = await compact_thread_context_with_llm(
                        runtime_db, **compaction_kwargs
                    )
                except Exception:
                    logger.warning(
                        "LLM compaction failed for thread %s; falling back "
                        "to text-based compaction",
                        thread_id,
                        exc_info=True,
                    )

                # Fall back to fast text-based compaction if LLM didn't trigger
                if compaction_result is None:
                    compaction_result = compact_thread_context(runtime_db, **compaction_kwargs)

                runtime_db.commit()
                if compaction_result is not None:
                    _refresh_companion_history(
                        user_id=user_id,
                        runtime_db=runtime_db,
                        thread_id=thread_id,
                    )
    except Exception:
        logger.exception("Post-turn compaction failed for thread %s", thread_id)


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
    source_run_id: int | None = None,
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
    _schedule_agent_experience_extraction(
        user_id=user_id,
        thread_id=thread_id,
        run_id=source_run_id,
        user_message=user_message,
        result=result,
        db_factory=db_factory,
    )
    schedule_reflection(
        user_id=user_id,
        thread_id=thread_id,
        db_factory=db_factory,
        runtime_db_factory=runtime_db_factory,
    )
    # Feedback signals (corrections/confirmations) feed FUTURE turns and the
    # correction path decrypts up to 50 memory items, so it runs in the
    # background.  It lives here rather than in turn-context assembly so it
    # fires only after the turn's rows are committed — spawning it mid-turn
    # raced the turn's own message writes on a shared DB connection.  Skip the
    # empty-message resume path (no new user turn → nothing to detect).
    if user_message.strip():
        _track_background_task(
            _process_feedback_signals_background(
                user_id=user_id,
                user_message=user_message,
                thread_id=thread_id,
                soul_db_factory=db_factory,
                runtime_db_factory=runtime_db_factory,
            )
        )


def _schedule_agent_experience_extraction(
    *,
    user_id: int,
    thread_id: int,
    run_id: int | None,
    user_message: str,
    result: AgentResult,
    db_factory: Callable[[], Session],
) -> None:
    if run_id is None or not user_message.strip():
        return
    if not _should_capture_agent_experience(result):
        return
    _track_background_task(
        _extract_agent_experience_in_background(
            user_id=user_id,
            thread_id=thread_id,
            run_id=run_id,
            user_message=user_message,
            result=result,
            db_factory=db_factory,
        )
    )


def _should_capture_agent_experience(result: AgentResult) -> bool:
    if result.stop_reason in {"cancelled", "error"}:
        return False
    tool_names = {
        tool_result.name
        for trace in result.step_traces
        for tool_result in trace.tool_results
        if tool_result.name != "send_message"
    }
    if tool_names:
        return True
    if len([trace for trace in result.step_traces if trace.llm_invoked]) >= 2:
        return True
    return len(_extract_inner_thoughts(result)) >= 200


async def _extract_agent_experience_in_background(
    *,
    user_id: int,
    thread_id: int,
    run_id: int | None,
    user_message: str,
    result: AgentResult,
    db_factory: Callable[[], Session],
) -> None:
    try:
        from anima_server.services.agent.agent_experience import (
            AgentExperienceCandidate,
            assign_experience_to_cluster,
            maybe_distill_skill_for_cluster,
            store_agent_experience,
        )
        from anima_server.services.agent.embeddings import generate_embedding

        tool_names = _experience_tool_names(result)
        task_intent = _experience_task_intent(user_message, tool_names)
        approach = _experience_approach(result)
        embedding = await generate_embedding(task_intent)
        with db_factory() as db:
            experience = store_agent_experience(
                db,
                user_id=user_id,
                candidate=AgentExperienceCandidate(
                    task_intent=task_intent,
                    approach=approach,
                    quality_score=_experience_quality(result),
                    source_thread_id=thread_id,
                    source_run_id=run_id,
                    tool_names=tuple(tool_names),
                    turn_count=max(1, len(result.step_traces)),
                    embedding=embedding,
                ),
            )
            cluster_id = assign_experience_to_cluster(
                db,
                user_id=user_id,
                experience=experience,
            )
            if cluster_id is not None:
                maybe_distill_skill_for_cluster(db, user_id=user_id, cluster_id=cluster_id)
            db.commit()
    except Exception:
        logger.debug("Agent experience extraction skipped for user %s", user_id, exc_info=True)


def _experience_tool_names(result: AgentResult) -> list[str]:
    names: list[str] = []
    for trace in result.step_traces:
        for tool_result in trace.tool_results:
            if tool_result.name == "send_message":
                continue
            if tool_result.name not in names:
                names.append(tool_result.name)
    for name in result.tools_used:
        if name != "send_message" and name not in names:
            names.append(name)
    return names


def _experience_task_intent(user_message: str, tool_names: Sequence[str]) -> str:
    prepared = " ".join((user_message or "").split()).strip()
    if len(prepared) > 180:
        prepared = prepared[:177].rstrip() + "..."
    if tool_names:
        return f"Handle user request with {', '.join(tool_names)}: {prepared}"
    return f"Handle multi-step user request: {prepared}"


def _experience_approach(result: AgentResult) -> str:
    lines: list[str] = []
    for trace in result.step_traces:
        if trace.reasoning_content:
            lines.append(f"Reasoning: {trace.reasoning_content.strip()[:500]}")
        for tool_call in trace.tool_calls:
            lines.append(f"Tried: {tool_call.name}")
        for tool_result in trace.tool_results:
            outcome = "failed" if tool_result.is_error else "succeeded"
            lines.append(f"Result: {tool_result.name} {outcome}.")
            if tool_result.inner_thinking:
                lines.append(f"Lesson: {tool_result.inner_thinking.strip()[:500]}")
    if result.response:
        lines.append(f"Outcome: {result.response.strip()[:700]}")
    return "\n".join(lines) or "Completed the task and responded to the user."


def _experience_quality(result: AgentResult) -> float:
    any_error = any(
        tool_result.is_error for trace in result.step_traces for tool_result in trace.tool_results
    )
    if any_error and result.response:
        return 0.55
    if any_error:
        return 0.25
    if result.stop_reason == "max_steps":
        return 0.45
    return 0.78


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
    async def run_turn(emit: Callable[[AgentStreamEvent], Awaitable[None]]) -> None:
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

    async with contextlib.aclosing(
        _stream_via_queue(
            run_turn,
            failure_log=f"Agent turn failed for user {user_id}",
        )
    ) as stream:
        async for event in stream:
            yield event


def list_agent_history(
    user_id: int, runtime_db: Session, *, limit: int = 50
) -> list[RuntimeMessage]:
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
                _track_background_task(close_task)
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
