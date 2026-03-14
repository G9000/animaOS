from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_db
from anima_server.schemas.chat import (
    ChatRequest,
    ChatResetRequest,
    ChatResetResponse,
    ChatResponse,
)
from anima_server.services.agent import (
    ensure_agent_ready,
    reset_agent_thread,
    run_agent,
    stream_agent,
)
from anima_server.services.agent.llm import LLMConfigError
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
        except (LLMConfigError, PromptTemplateError) as exc:
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
    except (LLMConfigError, PromptTemplateError) as exc:
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
