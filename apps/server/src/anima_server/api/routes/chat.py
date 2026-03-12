from __future__ import annotations

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.schemas.chat import (
    ChatRequest,
    ChatResetRequest,
    ChatResetResponse,
    ChatResponse,
)
from anima_server.services.agent_graph import reset_agent_thread, run_agent, stream_agent

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def send_message(
    payload: ChatRequest,
    request: Request,
) -> ChatResponse | StreamingResponse:
    require_unlocked_user(request, payload.userId)

    if not payload.stream:
        result = await run_agent(payload.message, payload.userId)
        return ChatResponse(
            response=result.response,
            model=result.model,
            provider=result.provider,
            toolsUsed=result.tools_used,
        )

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for chunk in stream_agent(payload.message, payload.userId):
                yield _format_sse_event("chunk", {"content": chunk})
            yield _format_sse_event("done", {"status": "complete"})
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
) -> ChatResetResponse:
    require_unlocked_user(request, payload.userId)
    await reset_agent_thread(payload.userId)
    return ChatResetResponse(status="reset")


def _format_sse_event(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
