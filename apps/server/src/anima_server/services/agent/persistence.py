from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from anima_server.models import AgentMessage, AgentRun, AgentStep, AgentThread
from anima_server.services.agent.compaction import estimate_message_tokens
from anima_server.services.agent.runtime_types import StepTrace, ToolCall, UsageStats
from anima_server.services.agent.state import AgentResult, StoredMessage


def get_or_create_thread(db: Session, user_id: int) -> AgentThread:
    thread = db.scalar(select(AgentThread).where(AgentThread.user_id == user_id))
    if thread is not None:
        return thread

    thread = AgentThread(
        user_id=user_id,
        status="active",
    )
    db.add(thread)
    db.flush()
    return thread


def load_thread_history(db: Session, thread_id: int) -> list[StoredMessage]:
    rows = db.scalars(
        select(AgentMessage)
        .where(
            AgentMessage.thread_id == thread_id,
            AgentMessage.is_in_context.is_(True),
            AgentMessage.role.in_(("summary", "user", "assistant", "tool")),
        )
        .order_by(
            case((AgentMessage.role == "summary", 0), else_=1),
            AgentMessage.sequence_id,
        )
    ).all()

    history: list[StoredMessage] = []
    for row in rows:
        history.append(
            StoredMessage(
                role=row.role,
                content=row.content_text or "",
                tool_name=row.tool_name,
                tool_call_id=row.tool_call_id,
                tool_calls=_deserialize_tool_calls(row.content_json),
            )
        )
    return history


def create_run(
    db: Session,
    *,
    thread_id: int,
    user_id: int,
    provider: str,
    model: str,
    mode: str,
) -> AgentRun:
    run = AgentRun(
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
    thread: AgentThread,
    run_id: int,
    content: str,
    sequence_id: int,
) -> AgentMessage:
    return append_message(
        db,
        thread=thread,
        run_id=run_id,
        step_id=None,
        sequence_id=sequence_id,
        role="user",
        content_text=content,
    )


def persist_agent_result(
    db: Session,
    *,
    thread: AgentThread,
    run: AgentRun,
    result: AgentResult,
    initial_sequence_id: int,
) -> None:
    sequence_id = initial_sequence_id

    for trace in result.step_traces:
        step = create_step(
            db,
            thread_id=thread.id,
            run_id=run.id,
            trace=trace,
        )

        if trace.assistant_text or trace.tool_calls:
            append_message(
                db,
                thread=thread,
                run_id=run.id,
                step_id=step.id,
                sequence_id=sequence_id,
                role="assistant",
                content_text=trace.assistant_text or None,
                content_json={
                    "tool_calls": [asdict(tool_call) for tool_call in trace.tool_calls]
                }
                if trace.tool_calls
                else None,
            )
            sequence_id += 1

        for tool_result in trace.tool_results:
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
            sequence_id += 1

    finalize_run(db, run=run, result=result)


def mark_run_failed(db: Session, run: AgentRun, error_text: str) -> None:
    run.status = "failed"
    run.error_text = error_text
    run.completed_at = datetime.now(UTC)
    db.add(run)


def reset_thread(db: Session, user_id: int) -> None:
    thread = db.scalar(select(AgentThread).where(AgentThread.user_id == user_id))
    if thread is None:
        return

    db.delete(thread)


def clear_threads(db: Session) -> None:
    db.execute(delete(AgentThread))


def count_messages_by_role(db: Session, thread_id: int, role: str) -> int:
    count = db.scalar(
        select(func.count(AgentMessage.id)).where(
            AgentMessage.thread_id == thread_id,
            AgentMessage.role == role,
        )
    )
    return int(count or 0)


def next_sequence_id(db: Session, thread_id: int) -> int:
    max_sequence = db.scalar(
        select(func.max(AgentMessage.sequence_id)).where(AgentMessage.thread_id == thread_id)
    )
    return (int(max_sequence) if max_sequence is not None else 0) + 1


def append_message(
    db: Session,
    *,
    thread: AgentThread,
    run_id: int | None,
    step_id: int | None,
    sequence_id: int,
    role: str,
    content_text: str | None,
    content_json: dict[str, object] | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    tool_args_json: dict[str, object] | None = None,
) -> AgentMessage:
    timestamp = datetime.now(UTC)
    message = AgentMessage(
        thread_id=thread.id,
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
        token_estimate=estimate_message_tokens(
            content_text=content_text,
            content_json=content_json,
            tool_name=tool_name,
        ),
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
) -> AgentStep:
    step = AgentStep(
        thread_id=thread_id,
        run_id=run_id,
        step_index=trace.step_index,
        status="completed",
        request_json={
            "messages": [asdict(message) for message in trace.request_messages],
            "allowed_tools": list(trace.allowed_tools),
            "force_tool_call": trace.force_tool_call,
        },
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
    run: AgentRun,
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
            )
        )

    return tuple(tool_calls)
