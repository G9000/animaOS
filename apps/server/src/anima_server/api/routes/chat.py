from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from anima_server.api.deps.unlock import require_unlocked_user
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

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def send_message(
    payload: ChatRequest,
    request: Request,
) -> ChatResponse | StreamingResponse:
    require_unlocked_user(request, payload.userId)

    if not payload.stream:
        try:
            result = await run_agent(payload.message, payload.userId)
        except LLMConfigError as exc:
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
    except LLMConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    async def event_stream() -> AsyncGenerator[str, None]:
        async for chunk in stream_agent(payload.message, payload.userId):
            yield _format_sse_event("chunk", {"content": chunk})
        yield _format_sse_event("done", {"status": "complete"})

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
) -> ChatResetResponse:
    require_unlocked_user(request, payload.userId)
    await reset_agent_thread(payload.userId)
    return ChatResetResponse(status="reset")


def _format_sse_event(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
