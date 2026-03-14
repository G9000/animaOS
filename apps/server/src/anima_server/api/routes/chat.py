from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
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
from anima_server.services.agent.system_prompt import PromptTemplateError

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
        except Exception as exc:
            yield _format_sse_event("error", {"error": str(exc)})

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
            role=row.role,
            content=row.content_text or "",
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


def _format_sse_event(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
