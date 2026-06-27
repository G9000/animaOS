from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import get_request_context
from anima_server.auth.context import GatewayRequestContext
from anima_server.auth.policy import webhook_idempotency_store
from anima_server.db import get_db, get_runtime_db
from anima_server.services.gateway_runtime import RuntimeInvocationError, execute_chat_non_stream


router = APIRouter(prefix="/api/webhook", tags=["webhook"])


class WebhookEvent(BaseModel):
    event_id: str | None = Field(default=None, alias="eventId")
    userId: int | None = Field(default=None, ge=0)
    message: str | None = None
    threadId: int | None = Field(default=None, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class WebhookResponse(BaseModel):
    status: str
    provider: str
    eventId: str
    duplicate: bool = False
    userId: int | None = None
    runtimeResponse: str | None = None
    runtimeModel: str | None = None
    runtimeProvider: str | None = None


def _normalize_user_id(event: WebhookEvent, payload: dict[str, Any]) -> int | None:
    if event.userId is not None:
        return event.userId
    candidate = payload.get("userId")
    if isinstance(candidate, int) and candidate >= 0:
        return candidate
    alt = payload.get("user_id")
    if isinstance(alt, int) and alt >= 0:
        return alt
    return None


def _normalize_message(event: WebhookEvent, payload: dict[str, Any]) -> str:
    if event.message:
        message = event.message.strip()
        if message:
            return message

    candidates = [
        payload.get("message"),
        payload.get("text"),
        payload.get("content"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            message = candidate.strip()
            if message:
                return message

    nested_payload = payload.get("data")
    if isinstance(nested_payload, dict):
        for candidate in (nested_payload.get("message"), nested_payload.get("text")):
            if isinstance(candidate, str):
                candidate_value = candidate.strip()
                if candidate_value:
                    return candidate_value
    return ""


def _response_cache_key(
    provider: str,
    event_id: str | None,
) -> str:
    if event_id:
        return f"webhook:{provider}:{event_id}"
    return f"webhook:{provider}:{uuid4()}"


def _create_webhook_response(
    *,
    provider: str,
    event_id: str,
    duplicate: bool,
    user_id: int | None,
    runtime_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "duplicate" if duplicate else "accepted",
        "provider": provider,
        "eventId": event_id,
        "duplicate": duplicate,
        "userId": user_id,
        "runtimeResponse": runtime_output.get("response") if runtime_output else None,
        "runtimeModel": runtime_output.get("model") if runtime_output else None,
        "runtimeProvider": runtime_output.get("provider") if runtime_output else None,
    }


@router.post("/{provider}")
async def ingest_webhook_event(
    provider: str = Path(..., min_length=1),
    event: WebhookEvent,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> WebhookResponse:
    context: GatewayRequestContext = get_request_context(request)
    body_payload = dict(event.payload)
    user_id = _normalize_user_id(event, body_payload)
    message = _normalize_message(event, body_payload)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload is missing userId.",
        )
    if not message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload must include message content.",
        )
    event_id = (
        event.event_id
        or request.headers.get("x-webhook-event-id")
        or request.headers.get("x-request-id")
        or str(uuid4())
    )
    cache_key = _response_cache_key(provider, event_id)
    is_first, cached = webhook_idempotency_store.check(cache_key)
    if not is_first:
        if isinstance(cached, dict):
            cached_payload = dict(cached)
        else:
            cached_payload = {"status": "duplicate", "provider": provider, "event_id": event_id}
        cached_payload["duplicate"] = True
        return WebhookResponse(**cached_payload)

    normalized_context = GatewayRequestContext(
        auth_token=context.auth_token,
        token_type=context.token_type,
        user_id=user_id,
        device_id=context.device_id,
        device_secret=context.device_secret,
        request_id=context.request_id or str(uuid4()),
        trace_id=context.trace_id,
        session_id=context.session_id,
        session=None,
        path=f"/api/webhook/{provider}",
    )
    try:
        runtime_result = await execute_chat_non_stream(
            context=normalized_context,
            db=db,
            runtime_db=runtime_db,
            user_id=user_id,
            message=message,
            source=f"webhook:{provider}",
            thread_id=event.threadId,
            document_ids=(),
            context_messages=(),
            today_context=None,
            attachments=(),
        )
    except RuntimeInvocationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    runtime_payload = {
        "response": runtime_result.response,
        "model": runtime_result.model,
        "provider": runtime_result.provider,
    }
    response = _create_webhook_response(
        provider=provider,
        event_id=event_id,
        duplicate=False,
        user_id=user_id,
        runtime_output=runtime_payload,
    )
    webhook_idempotency_store.mark(cache_key, response)
    return WebhookResponse(**response)
