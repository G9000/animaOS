from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db, get_runtime_db
from anima_server.db.session import build_session_factory_for_db
from anima_server.models import MemoryItem, Task
from anima_server.models.runtime import RuntimeMessage, RuntimeRun, RuntimeThread
from anima_server.schemas.chat import (
    ApprovalRequest,
    ApprovalResponse,
    CancelRunRequest,
    CancelRunResponse,
    ChatHistoryClearResponse,
    ChatHistoryMessage,
    ChatRequest,
    ChatResetRequest,
    ChatResetResponse,
    ChatResponse,
    DryRunRequest,
    DryRunResponse,
)
from anima_server.services.agent import (
    approve_or_deny_turn,
    cancel_agent_run,
    dry_run_agent,
    ensure_agent_ready,
    list_agent_history,
    reset_agent_thread,
    run_agent,
    stream_agent,
    stream_approve_or_deny,
)
from anima_server.services.agent.llm import LLMConfigError, LLMInvocationError
from anima_server.services.agent.memory_store import get_current_focus
from anima_server.services.agent.system_prompt import PromptTemplateError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def send_message(
    payload: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> ChatResponse | StreamingResponse:
    require_unlocked_user(request, payload.userId)

    if not payload.stream:
        try:
            result = await run_agent(payload.message, payload.userId, db, runtime_db, source=payload.source)
        except (LLMConfigError, LLMInvocationError, PromptTemplateError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        return ChatResponse(
            response=result.response,
            model=result.model,
            provider=result.provider,
            toolsUsed=result.tools_used,
        )

    try:
        ensure_agent_ready()
    except (LLMConfigError, LLMInvocationError, PromptTemplateError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for event in stream_agent(
                payload.message, payload.userId, db, runtime_db, source=payload.source
            ):
                if event.event == "thought":
                    continue  # private reasoning, not forwarded to client
                yield _format_sse_event(event.event, event.data)
        except (LLMConfigError, LLMInvocationError, PromptTemplateError) as exc:
            yield _format_sse_event("error", {"error": str(exc)})
        except Exception:
            logger.exception("Unexpected error during SSE streaming")
            yield _format_sse_event(
                "error", {"error": "An internal error occurred during streaming."}
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.get("/history", response_model=list[ChatHistoryMessage])
async def get_chat_history(
    request: Request,
    userId: int = Query(ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    runtime_db: Session = Depends(get_runtime_db),
) -> list[ChatHistoryMessage]:
    require_unlocked_user(request, userId)
    rows = list_agent_history(userId, runtime_db, limit=limit)
    return [
        ChatHistoryMessage(
            id=row.id,
            userId=userId,
            role="assistant" if row.role == "tool" else row.role,
            content=row.content_text or "",
            createdAt=row.created_at,
            source=getattr(row, "source", None),
        )
        for row in rows
    ]


@router.delete("/history", response_model=ChatHistoryClearResponse)
async def clear_chat_history(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> ChatHistoryClearResponse:
    require_unlocked_user(request, payload.userId)
    await reset_agent_thread(payload.userId, runtime_db, db=db)
    return ChatHistoryClearResponse(status="cleared")


@router.post("/reset", response_model=ChatResetResponse)
async def reset_chat_thread(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> ChatResetResponse:
    require_unlocked_user(request, payload.userId)
    await reset_agent_thread(payload.userId, runtime_db, db=db)
    return ChatResetResponse(status="reset")


@router.get("/brief")
async def get_brief(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Quick context brief (static, no LLM). Use /greeting for personalized greetings."""
    require_unlocked_user(request, userId)

    from anima_server.services.agent.proactive import (
        build_static_greeting,
        gather_greeting_context,
    )

    ctx = gather_greeting_context(db, user_id=userId)
    return {
        "message": build_static_greeting(ctx),
        "context": {
            "currentFocus": ctx.current_focus,
            "openTaskCount": ctx.open_task_count,
            "daysSinceLastChat": ctx.days_since_last_chat,
        },
    }


@router.get("/greeting")
async def get_greeting(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Generate a personalized greeting using the agent's self-model and context.

    Uses LLM when available, falls back to static greeting otherwise.
    """
    require_unlocked_user(request, userId)

    from anima_server.services.agent.proactive import generate_greeting

    result = await generate_greeting(db, user_id=userId)
    return {
        "message": result.message,
        "llmGenerated": result.llm_generated,
        "context": {
            "currentFocus": result.context.current_focus,
            "openTaskCount": result.context.open_task_count,
            "overdueTasks": result.context.overdue_task_count,
            "daysSinceLastChat": result.context.days_since_last_chat,
            "upcomingDeadlines": list(result.context.upcoming_deadlines),
        },
    }


@router.get("/nudges")
async def get_nudges(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, object]]]:
    require_unlocked_user(request, userId)

    nudges: list[dict[str, object]] = []

    overdue_count = (
        db.scalar(
            select(func.count(Task.id)).where(
                Task.user_id == userId,
                Task.done.is_(False),
                Task.due_date.isnot(None),
                Task.due_date < func.date("now"),
            )
        )
        or 0
    )
    if overdue_count:
        nudges.append(
            {
                "type": "overdue_tasks",
                "message": f"You have {overdue_count} overdue task{'s' if overdue_count != 1 else ''}.",
                "priority": 3,
            }
        )

    return {"nudges": nudges}


@router.get("/home")
async def get_home(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    require_unlocked_user(request, userId)

    focus = get_current_focus(db, user_id=userId)

    tasks = list(
        db.scalars(
            select(Task)
            .where(Task.user_id == userId, Task.done.is_(False))
            .order_by(Task.priority.desc(), Task.created_at.desc())
            .limit(10)
        ).all()
    )

    memory_count = (
        db.scalar(
            select(func.count(MemoryItem.id)).where(
                MemoryItem.user_id == userId,
                MemoryItem.superseded_by.is_(None),
            )
        )
        or 0
    )

    message_count = (
        runtime_db.scalar(
            select(func.count(RuntimeMessage.id))
            .join(RuntimeThread, RuntimeMessage.thread_id == RuntimeThread.id)
            .where(RuntimeThread.user_id == userId)
        )
        or 0
    )

    journal_total = (
        runtime_db.scalar(
            select(func.count(func.distinct(func.date(RuntimeMessage.created_at)))).where(
                RuntimeMessage.user_id == userId,
                RuntimeMessage.role == "user",
            )
        )
        or 0
    )

    journal_streak = 0
    if journal_total > 0:
        from datetime import UTC, datetime, timedelta

        today = datetime.now(UTC).date()
        day = today
        while True:
            has_log = (
                runtime_db.scalar(
                    select(func.count(RuntimeMessage.id)).where(
                        RuntimeMessage.user_id == userId,
                        RuntimeMessage.role == "user",
                        func.date(RuntimeMessage.created_at) == day.isoformat(),
                    )
                )
                or 0
            )
            if has_log:
                journal_streak += 1
                day -= timedelta(days=1)
            else:
                break

    return {
        "currentFocus": focus,
        "tasks": [
            {
                "id": t.id,
                "text": t.text,
                "done": t.done,
                "priority": t.priority,
                "dueDate": t.due_date,
            }
            for t in tasks
        ],
        "journalStreak": journal_streak,
        "journalTotal": journal_total,
        "memoryCount": memory_count,
        "messageCount": message_count,
    }


@router.post("/consolidate")
async def consolidate(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Trigger memory extraction for recent conversations.

    Creates MemoryCandidate rows via run_background_extraction. The Soul
    Writer will batch-promote them into the soul store when enough
    candidates accumulate.
    """
    require_unlocked_user(request, payload.userId)

    from anima_server.services.agent.consolidation import run_background_extraction

    messages = list(
        runtime_db.scalars(
            select(RuntimeMessage)
            .where(
                RuntimeMessage.user_id == payload.userId,
                RuntimeMessage.role.in_(("user", "assistant")),
            )
            .order_by(RuntimeMessage.created_at.desc())
            .limit(20)
        ).all()
    )

    # Pair consecutive user/assistant messages for extraction
    pairs: list[tuple[str, str]] = []
    msgs = list(reversed(messages))
    i = 0
    while i < len(msgs) - 1:
        if msgs[i].role == "user" and msgs[i + 1].role == "assistant":
            pairs.append((msgs[i].content_text or "", msgs[i + 1].content_text or ""))
            i += 2
        else:
            i += 1

    rt_factory = build_session_factory_for_db(runtime_db)
    candidates_created = 0
    errors: list[str] = []
    for user_message, assistant_response in pairs:
        try:
            await run_background_extraction(
                user_id=payload.userId,
                user_message=user_message,
                assistant_response=assistant_response,
                runtime_db_factory=rt_factory,
            )
            candidates_created += 1
        except Exception as exc:
            errors.append(str(exc))

    return {"filesProcessed": len(pairs), "filesChanged": candidates_created, "errors": errors}


@router.post("/sleep")
async def trigger_sleep_tasks(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Manually trigger sleep-time maintenance tasks (contradiction scan, profile synthesis, etc.)."""
    require_unlocked_user(request, payload.userId)

    from anima_server.services.agent.sleep_tasks import run_sleep_tasks

    result = await run_sleep_tasks(
        user_id=payload.userId,
        db_factory=build_session_factory_for_db(db),
    )
    return {
        "contradictionsFound": result.contradictions_found,
        "contradictionsResolved": result.contradictions_resolved,
        "itemsMerged": result.items_merged,
        "episodesGenerated": result.episodes_generated,
        "embeddingsBackfilled": result.embeddings_backfilled,
        "errors": result.errors,
    }


@router.post("/reflect")
async def trigger_deep_monologue(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Manually trigger a deep inner monologue (full self-model reflection)."""
    require_unlocked_user(request, payload.userId)

    from anima_server.services.agent.inner_monologue import run_deep_monologue

    result = await run_deep_monologue(
        user_id=payload.userId,
        db_factory=build_session_factory_for_db(db),
    )
    return {
        "identityUpdated": result.identity_updated,
        "innerStateUpdated": result.inner_state_updated,
        "workingMemoryUpdated": result.working_memory_updated,
        "growthLogEntryAdded": result.growth_log_entry_added,
        "intentionsUpdated": result.intentions_updated,
        "proceduralRulesAdded": result.procedural_rules_added,
        "insightsGenerated": result.insights_generated,
        "errors": result.errors,
    }


def _format_sse_event(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/runs/{run_id}/cancel", response_model=CancelRunResponse)
async def cancel_run(
    run_id: int,
    payload: CancelRunRequest,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> CancelRunResponse:
    """Request cancellation of a running agent turn."""
    require_unlocked_user(request, payload.userId)

    run = runtime_db.get(RuntimeRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.user_id != payload.userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to cancel this run"
        )
    cancelled = await cancel_agent_run(run_id, payload.userId, runtime_db)
    if cancelled is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return CancelRunResponse(runId=cancelled.id, status=cancelled.status)


@router.post("/dry-run", response_model=DryRunResponse)
async def dry_run(
    payload: DryRunRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> DryRunResponse:
    """Assemble the full prompt without calling the LLM."""
    require_unlocked_user(request, payload.userId)

    try:
        result = await dry_run_agent(payload.message, payload.userId, db, runtime_db)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return DryRunResponse(
        systemPrompt=result.system_prompt,
        messages=[{"role": m.role, "content": m.content} for m in result.messages],
        allowedTools=list(result.allowed_tools),
        estimatedPromptTokens=result.estimated_prompt_tokens,
        toolSchemas=list(result.tool_schemas),
        memoryBlockCount=len(result.memory_blocks),
    )


@router.post("/runs/{run_id}/approval", response_model=ApprovalResponse)
async def handle_approval(
    run_id: int,
    payload: ApprovalRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> ApprovalResponse | StreamingResponse:
    """Approve or deny a pending tool call for an awaiting-approval run."""
    require_unlocked_user(request, payload.userId)

    run = runtime_db.get(RuntimeRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    if run.user_id != payload.userId:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this run"
        )
    if run.status != "awaiting_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run is not awaiting approval (status: {run.status})",
        )

    if payload.stream:

        async def _generate() -> AsyncGenerator[str, None]:
            async for event in stream_approve_or_deny(
                run_id,
                payload.userId,
                payload.approved,
                db,
                runtime_db,
                denial_reason=payload.reason,
            ):
                if event.event == "thought":
                    continue  # private reasoning, not forwarded to client
                yield _format_sse_event(event.event, event.data)

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        result = await approve_or_deny_turn(
            run_id,
            payload.userId,
            payload.approved,
            db,
            runtime_db,
            denial_reason=payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    return ApprovalResponse(
        runId=run_id,
        status="completed",
        response=result.response,
        model=result.model,
        provider=result.provider,
        toolsUsed=list(result.tools_used),
    )
