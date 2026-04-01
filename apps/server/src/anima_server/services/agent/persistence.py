from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import and_, delete, desc, func, or_, select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeMessage, RuntimeRun, RuntimeStep, RuntimeThread
from anima_server.services.agent.compaction import estimate_message_tokens
from anima_server.services.agent.runtime_types import StepTrace, ToolCall, UsageStats
from anima_server.services.agent.state import AgentResult, StoredMessage


def get_or_create_thread(db: Session, user_id: int) -> RuntimeThread:
    thread = db.scalar(
        select(RuntimeThread).where(
            RuntimeThread.user_id == user_id,
            RuntimeThread.status == "active",
        )
    )
    if thread is not None:
        return thread

    thread = RuntimeThread(
        user_id=user_id,
        status="active",
    )
    db.add(thread)
    db.flush()
    return thread


def load_thread_history(
    db: Session, thread_id: int, *, user_id: int | None = None
) -> list[StoredMessage]:
    rows = db.scalars(
        select(RuntimeMessage)
        .where(
            RuntimeMessage.thread_id == thread_id,
            RuntimeMessage.is_in_context.is_(True),
            RuntimeMessage.is_archived_history.is_(False),
            RuntimeMessage.role.in_(("user", "assistant", "tool")),
        )
        .order_by(RuntimeMessage.sequence_id)
    ).all()

    history: list[StoredMessage] = []
    for row in rows:
        content = row.content_text or ""
        history.append(
            StoredMessage(
                role=row.role,
                content=content,
                tool_name=row.tool_name,
                tool_call_id=row.tool_call_id,
                tool_calls=_deserialize_tool_calls(row.content_json),
            )
        )
    return history


def list_transcript_messages(
    db: Session,
    *,
    thread_id: int | None = None,
    user_id: int | None = None,
    limit: int | None = None,
) -> list[RuntimeMessage]:
    if thread_id is not None:
        has_content = and_(
            RuntimeMessage.content_text.is_not(None),
            RuntimeMessage.content_text != "",
        )
        return list(
            db.scalars(
                select(RuntimeMessage)
                .where(
                    RuntimeMessage.thread_id == thread_id,
                    RuntimeMessage.role.notin_(("system", "approval")),
                    or_(
                        RuntimeMessage.is_in_context.is_(True),
                        has_content,
                    ),
                )
                .order_by(RuntimeMessage.sequence_id)
            ).all()
        )

    if user_id is None or limit is None:
        raise TypeError("list_transcript_messages requires thread_id or user_id with limit")

    thread = get_or_create_thread(db, user_id)

    rows = db.scalars(
        select(RuntimeMessage)
        .outerjoin(RuntimeRun, RuntimeMessage.run_id == RuntimeRun.id)
        .where(
            RuntimeMessage.thread_id == thread.id,
            RuntimeMessage.role.in_(("user", "assistant", "system", "tool")),
            RuntimeMessage.content_text.is_not(None),
            RuntimeMessage.content_text != "",
            or_(RuntimeMessage.run_id.is_(None), RuntimeRun.status.notin_(("failed", "cancelled"))),
        )
        .order_by(desc(RuntimeMessage.sequence_id))
        .limit(limit)
    ).all()
    rows.reverse()
    return [row for row in rows if not row.is_internal]


def close_thread(db: Session, *, thread_id: int) -> bool:
    """Mark a thread as closed.

    Returns True if the thread changed state, False if the thread is missing
    or was already closed.
    """
    thread = db.get(RuntimeThread, thread_id)
    if thread is None or thread.status == "closed":
        return False

    thread.status = "closed"
    thread.closed_at = datetime.now(UTC)
    db.add(thread)
    db.flush()
    return True


def create_run(
    db: Session,
    *,
    thread_id: int,
    user_id: int,
    provider: str,
    model: str,
    mode: str,
) -> RuntimeRun:
    run = RuntimeRun(
        thread_id=thread_id,
        user_id=user_id,
        provider=provider,
        model=model,
        mode=mode,
        status="running",
    )
    db.add(run)
    db.flush()
    return run


def append_user_message(
    db: Session,
    *,
    thread: RuntimeThread,
    run_id: int,
    content: str,
    sequence_id: int,
    source: str | None = None,
) -> RuntimeMessage:
    return append_message(
        db,
        thread=thread,
        run_id=run_id,
        step_id=None,
        sequence_id=sequence_id,
        role="user",
        content_text=content,
        source=source,
    )


def persist_agent_result(
    db: Session,
    *,
    thread: RuntimeThread,
    run: RuntimeRun,
    result: AgentResult,
    initial_sequence_id: int | None,
) -> None:
    sequence_id = initial_sequence_id

    for trace_index, trace in enumerate(result.step_traces):
        step = create_step(
            db,
            thread_id=thread.id,
            run_id=run.id,
            trace=trace,
            prompt_budget=result.prompt_budget if trace_index == 0 else None,
        )

        if trace.assistant_text or trace.tool_calls:
            if sequence_id is None:
                raise RuntimeError("Missing reserved message sequence for assistant output.")
            # Use extracted inner thinking as content (thinking kwarg
            # value stored as assistant message content), but only when
            # the step contains exclusively non-terminal tool calls.
            # If ANY tool result is terminal (send_message), skip
            # thinking persistence entirely — to_runtime_message()
            # won't re-inject for steps containing send_message, so
            # stored thinking would leak as visible assistant text.
            has_terminal = any(tr.is_terminal for tr in trace.tool_results)
            inner_thinking: str | None = None
            if not has_terminal:
                inner_thinking = next(
                    (
                        tr.inner_thinking.strip()
                        for tr in trace.tool_results
                        if tr.inner_thinking and tr.inner_thinking.strip()
                    ),
                    None,
                )
            # When non-terminal tool calls exist but no inner_thinking
            # was extracted, store None — not assistant_text, which may
            # contain coerced tool syntax that would be wrongly
            # re-injected as ``thinking`` on history replay.
            # Terminal steps (send_message) keep assistant_text since
            # to_runtime_message() won't re-inject for those.
            if inner_thinking:
                content_text = inner_thinking
            elif has_terminal or not trace.tool_calls:
                content_text = trace.assistant_text or None
            else:
                content_text = None
            append_message(
                db,
                thread=thread,
                run_id=run.id,
                step_id=step.id,
                sequence_id=sequence_id,
                role="assistant",
                content_text=content_text,
                content_json={"tool_calls": [asdict(tool_call) for tool_call in trace.tool_calls]}
                if trace.tool_calls
                else None,
            )
            sequence_id = sequence_id + 1

        for tool_result in trace.tool_results:
            if sequence_id is None:
                raise RuntimeError("Missing reserved message sequence for tool output.")
            append_message(
                db,
                thread=thread,
                run_id=run.id,
                step_id=step.id,
                sequence_id=sequence_id,
                role="tool",
                content_text=tool_result.output,
                tool_name=tool_result.name,
                tool_call_id=tool_result.call_id,
            )
            sequence_id = sequence_id + 1

    finalize_run(db, run=run, result=result)


def mark_run_failed(db: Session, run: RuntimeRun, error_text: str) -> None:
    run.status = "failed"
    run.error_text = error_text
    run.completed_at = datetime.now(UTC)
    db.add(run)


def cancel_run(db: Session, run_id: int) -> RuntimeRun | None:
    """Mark a run as cancelled.  Returns the run, or None if not found.

    Idempotent: if the run is already terminal (completed, failed,
    cancelled) the existing row is returned without modification.

    If the run was awaiting approval, the pending approval message is
    marked out of context and the FK is cleared.
    """
    run = db.get(RuntimeRun, run_id)
    if run is None:
        return None
    if run.status in ("completed", "failed", "cancelled"):
        return run

    # Clean up pending approval if present.
    if run.pending_approval_message_id is not None:
        approval_msg = db.get(RuntimeMessage, run.pending_approval_message_id)
        if approval_msg is not None:
            approval_msg.is_in_context = False
            db.add(approval_msg)
        run.pending_approval_message_id = None

    run.status = "cancelled"
    run.stop_reason = "cancelled"
    run.completed_at = datetime.now(UTC)
    db.add(run)
    db.flush()
    return run


def save_approval_checkpoint(
    db: Session,
    *,
    thread: RuntimeThread,
    run: RuntimeRun,
    tool_call: ToolCall,
    step_id: int | None,
    sequence_id: int,
) -> RuntimeMessage:
    """Persist an approval-pending checkpoint as an ``approval`` role message.

    The run is updated to ``awaiting_approval`` status with a FK back to
    the approval message for fast lookup on resume.
    """
    from dataclasses import asdict

    approval_msg = append_message(
        db,
        thread=thread,
        run_id=run.id,
        step_id=step_id,
        sequence_id=sequence_id,
        role="approval",
        content_text=f"Approval required for tool: {tool_call.name}",
        content_json={"tool_calls": [asdict(tool_call)]},
        tool_name=tool_call.name,
        tool_call_id=tool_call.id,
        tool_args_json=tool_call.arguments if isinstance(tool_call.arguments, dict) else {},
    )

    run.status = "awaiting_approval"
    run.stop_reason = "awaiting_approval"
    run.pending_approval_message_id = approval_msg.id
    db.add(run)
    db.flush()
    return approval_msg


def load_approval_checkpoint(
    db: Session,
    run_id: int,
) -> tuple[RuntimeRun, RuntimeMessage] | None:
    """Load the run and its pending approval message.

    Returns ``None`` if the run doesn't exist or is not awaiting approval.
    """
    run = db.get(RuntimeRun, run_id)
    if run is None or run.status != "awaiting_approval":
        return None
    if run.pending_approval_message_id is None:
        return None
    approval_msg = db.get(RuntimeMessage, run.pending_approval_message_id)
    if approval_msg is None:
        return None
    return run, approval_msg


def clear_approval_checkpoint(
    db: Session,
    run: RuntimeRun,
    approval_msg: RuntimeMessage,
) -> None:
    """Mark approval resolved: message out of context, FK cleared."""
    approval_msg.is_in_context = False
    db.add(approval_msg)
    run.pending_approval_message_id = None
    db.add(run)
    db.flush()


def reset_thread(db: Session, user_id: int) -> None:
    thread = db.scalar(select(RuntimeThread).where(RuntimeThread.user_id == user_id))
    if thread is None:
        return

    db.delete(thread)


def clear_threads(db: Session) -> None:
    db.execute(delete(RuntimeThread))


def count_messages_by_role(db: Session, thread_id: int, role: str) -> int:
    count = db.scalar(
        select(func.count(RuntimeMessage.id)).where(
            RuntimeMessage.thread_id == thread_id,
            RuntimeMessage.role == role,
        )
    )
    return int(count or 0)


def append_message(
    db: Session,
    *,
    thread: RuntimeThread,
    run_id: int | None,
    step_id: int | None,
    sequence_id: int,
    role: str,
    content_text: str | None,
    content_json: dict[str, object] | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    tool_args_json: dict[str, object] | None = None,
    source: str | None = None,
    is_archived_history: bool = False,
) -> RuntimeMessage:
    timestamp = datetime.now(UTC)
    message = RuntimeMessage(
        thread_id=thread.id,
        user_id=thread.user_id,
        run_id=run_id,
        step_id=step_id,
        sequence_id=sequence_id,
        role=role,
        content_text=content_text,
        content_json=content_json,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_args_json=tool_args_json,
        is_in_context=True,
        is_archived_history=is_archived_history,
        token_estimate=estimate_message_tokens(
            content_text=content_text,
            content_json=content_json,
            tool_name=tool_name,
        ),
        source=source,
    )
    db.add(message)

    thread.updated_at = timestamp
    thread.last_message_at = timestamp
    db.add(thread)
    db.flush()
    return message


def create_step(
    db: Session,
    *,
    thread_id: int,
    run_id: int,
    trace: StepTrace,
    prompt_budget: object | None = None,
) -> RuntimeStep:
    request_json: dict[str, object] = {
        "messages": [asdict(message) for message in trace.request_messages],
        "allowed_tools": list(trace.allowed_tools),
        "force_tool_call": trace.force_tool_call,
    }
    if prompt_budget is not None:
        request_json["prompt_budget"] = asdict(prompt_budget)

    step = RuntimeStep(
        thread_id=thread_id,
        run_id=run_id,
        step_index=trace.step_index,
        status="completed",
        request_json=request_json,
        response_json={
            "assistant_text": trace.assistant_text,
            "tool_results": [asdict(result) for result in trace.tool_results],
        },
        tool_calls_json=[asdict(tool_call) for tool_call in trace.tool_calls] or None,
        usage_json=_serialize_usage(trace.usage),
    )
    db.add(step)
    db.flush()
    return step


def finalize_run(
    db: Session,
    *,
    run: RuntimeRun,
    result: AgentResult,
) -> None:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0

    for trace in result.step_traces:
        if trace.usage is None:
            continue
        prompt_tokens += trace.usage.prompt_tokens or 0
        completion_tokens += trace.usage.completion_tokens or 0
        total_tokens += trace.usage.total_tokens or 0

    run.status = "completed"
    run.provider = result.provider
    run.model = result.model
    run.stop_reason = result.stop_reason
    run.completed_at = datetime.now(UTC)
    run.prompt_tokens = prompt_tokens or None
    run.completion_tokens = completion_tokens or None
    run.total_tokens = total_tokens or None
    db.add(run)


def _serialize_usage(usage: UsageStats | None) -> dict[str, object] | None:
    if usage is None:
        return None
    return asdict(usage)


def list_threads(db: Session, user_id: int) -> list[RuntimeThread]:
    """Return all threads for a user sorted by last_message_at DESC."""
    from sqlalchemy import nulls_last

    return list(
        db.scalars(
            select(RuntimeThread)
            .where(RuntimeThread.user_id == user_id)
            .order_by(nulls_last(desc(RuntimeThread.last_message_at)))
        ).all()
    )


def create_thread(db: Session, user_id: int) -> RuntimeThread:
    """Create a new active thread, closing any existing active thread first."""
    existing = db.scalar(
        select(RuntimeThread).where(
            RuntimeThread.user_id == user_id,
            RuntimeThread.status == "active",
        )
    )
    if existing is not None:
        existing.status = "closed"
        existing.closed_at = datetime.now(UTC)
        db.add(existing)
        db.flush()

    thread = RuntimeThread(user_id=user_id, status="active")
    db.add(thread)
    db.flush()
    return thread


def _deserialize_tool_calls(
    content_json: dict[str, object] | None,
) -> tuple[ToolCall, ...]:
    if not isinstance(content_json, dict):
        return ()

    raw_tool_calls = content_json.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return ()

    tool_calls: list[ToolCall] = []
    for index, raw_tool_call in enumerate(raw_tool_calls):
        if not isinstance(raw_tool_call, dict):
            continue

        name = str(raw_tool_call.get("name", "")).strip()
        if not name:
            continue

        arguments = raw_tool_call.get("arguments", {})
        tool_calls.append(
            ToolCall(
                id=str(raw_tool_call.get("id") or f"tool-call-{index}"),
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
                parse_error=(
                    str(raw_tool_call.get("parse_error")).strip()
                    if isinstance(raw_tool_call.get("parse_error"), str)
                    and str(raw_tool_call.get("parse_error")).strip()
                    else None
                ),
                raw_arguments=(
                    str(raw_tool_call.get("raw_arguments"))[:500]
                    if isinstance(raw_tool_call.get("raw_arguments"), str)
                    and raw_tool_call.get("raw_arguments")
                    else None
                ),
            )
        )

    return tuple(tool_calls)
