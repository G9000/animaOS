from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from sqlalchemy import func, select

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.models import AgentMessage, AgentThread, MemoryDailyLog, MemoryItem, Task
from anima_server.schemas.chat import (
    ChatHistoryClearResponse,
    ChatHistoryMessage,
    ChatRequest,
    ChatResetRequest,
    ChatResetResponse,
    ChatResponse,
)
from anima_server.services.agent import (
    ensure_agent_ready,
    list_agent_history,
    reset_agent_thread,
    run_agent,
    stream_agent,
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
) -> ChatResponse | StreamingResponse:
    require_unlocked_user(request, payload.userId)

    if not payload.stream:
        try:
            result = await run_agent(payload.message, payload.userId, db)
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
            async for event in stream_agent(payload.message, payload.userId, db):
                yield _format_sse_event(event.event, event.data)
        except (LLMConfigError, LLMInvocationError, PromptTemplateError) as exc:
            yield _format_sse_event("error", {"error": str(exc)})
        except Exception:
            logger.exception("Unexpected error during SSE streaming")
            yield _format_sse_event("error", {"error": "An internal error occurred during streaming."})

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
    userId: int = Query(gt=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[ChatHistoryMessage]:
    require_unlocked_user(request, userId)
    rows = list_agent_history(userId, db, limit=limit)
    return [
        ChatHistoryMessage(
            id=row.id,
            userId=userId,
            role="assistant" if row.role == "tool" else row.role,
            content=row.content_text,
            createdAt=row.created_at,
        )
        for row in rows
    ]


@router.delete("/history", response_model=ChatHistoryClearResponse)
async def clear_chat_history(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ChatHistoryClearResponse:
    require_unlocked_user(request, payload.userId)
    await reset_agent_thread(payload.userId, db)
    return ChatHistoryClearResponse(status="cleared")


@router.post("/reset", response_model=ChatResetResponse)
async def reset_chat_thread(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ChatResetResponse:
    require_unlocked_user(request, payload.userId)
    await reset_agent_thread(payload.userId, db)
    return ChatResetResponse(status="reset")


@router.get("/brief")
async def get_brief(
    request: Request,
    userId: int = Query(gt=0),
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
    userId: int = Query(gt=0),
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
    userId: int = Query(gt=0),
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, object]]]:
    require_unlocked_user(request, userId)

    nudges: list[dict[str, object]] = []

    overdue_count = db.scalar(
        select(func.count(Task.id)).where(
            Task.user_id == userId,
            Task.done.is_(False),
            Task.due_date.isnot(None),
            Task.due_date < func.date("now"),
        )
    ) or 0
    if overdue_count:
        nudges.append({
            "type": "overdue_tasks",
            "message": f"You have {overdue_count} overdue task{'s' if overdue_count != 1 else ''}.",
            "priority": 3,
        })

    return {"nudges": nudges}


@router.get("/home")
async def get_home(
    request: Request,
    userId: int = Query(gt=0),
    db: Session = Depends(get_db),
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

    memory_count = db.scalar(
        select(func.count(MemoryItem.id)).where(
            MemoryItem.user_id == userId,
            MemoryItem.superseded_by.is_(None),
        )
    ) or 0

    message_count = db.scalar(
        select(func.count(AgentMessage.id)).join(
            AgentThread, AgentMessage.thread_id == AgentThread.id
        ).where(AgentThread.user_id == userId)
    ) or 0

    journal_total = db.scalar(
        select(func.count(func.distinct(MemoryDailyLog.date))).where(
            MemoryDailyLog.user_id == userId,
        )
    ) or 0

    journal_streak = 0
    if journal_total > 0:
        from datetime import UTC, datetime, timedelta

        today = datetime.now(UTC).date()
        day = today
        while True:
            has_log = db.scalar(
                select(func.count(MemoryDailyLog.id)).where(
                    MemoryDailyLog.user_id == userId,
                    MemoryDailyLog.date == day.isoformat(),
                )
            ) or 0
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
) -> dict[str, object]:
    """Trigger memory consolidation for recent conversations."""
    require_unlocked_user(request, payload.userId)

    from anima_server.services.agent.consolidation import consolidate_turn_memory_with_llm

    logs = list(
        db.scalars(
            select(MemoryDailyLog)
            .where(MemoryDailyLog.user_id == payload.userId)
            .order_by(MemoryDailyLog.created_at.desc())
            .limit(10)
        ).all()
    )

    items_added = 0
    errors: list[str] = []
    for log in logs:
        try:
            result = await consolidate_turn_memory_with_llm(
                user_id=payload.userId,
                user_message=log.user_message,
                assistant_response=log.assistant_response,
            )
            items_added += len(result.llm_items_added)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    return {"filesProcessed": len(logs), "filesChanged": items_added, "errors": errors}


@router.post("/sleep")
async def trigger_sleep_tasks(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Manually trigger sleep-time maintenance tasks (contradiction scan, profile synthesis, etc.)."""
    require_unlocked_user(request, payload.userId)

    from anima_server.services.agent.sleep_tasks import run_sleep_tasks

    result = await run_sleep_tasks(user_id=payload.userId)
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

    result = await run_deep_monologue(user_id=payload.userId)
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
