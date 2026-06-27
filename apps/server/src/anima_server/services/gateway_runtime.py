from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from time import perf_counter

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from anima_server.auth.context import GatewayRequestContext
from anima_server.contracts.runtime import (
    RuntimeChatInput,
    RuntimeChatOutput,
    RuntimeRequestContext,
    RuntimeStreamEvent,
)
from anima_server.schemas.chat import (
    ChatContextMessage,
    ChatRequestAttachment,
    TodayContext,
)
from anima_server.services.agent import (
    ensure_agent_ready,
    normalize_document_only_user_message,
    run_agent,
    stream_agent,
)
from anima_server.services.agent.attachments import AttachmentTooLargeError, AttachmentValidationError
from anima_server.services.agent.llm import LLMConfigError, LLMInvocationError
from anima_server.services.agent.runtime_types import UsageStats
from anima_server.services.agent.state import serialize_agent_retrieval
from anima_server.services.agent.streaming import summarize_usage
from anima_server.services.health.event_logger import emit
from anima_server.services.health.event_logger import emit as health_emit
from anima_server.services.agent.system_prompt import PromptTemplateError


_CHAT_TIMEOUT_SECONDS = 120


class RuntimeInvocationError(HTTPException):
    """Domain error wrapper for contract-level runtime failures."""


def _contract_context(context: GatewayRequestContext) -> RuntimeRequestContext:
    return RuntimeRequestContext(
        user_id=context.user_id or 0,
        trace_id=context.trace_id,
        request_id=context.request_id,
        session_id=context.session_id,
        device_id=context.device_id,
        path=context.path,
        source=_derive_source(context),
    )


def _derive_source(context: GatewayRequestContext) -> str:
    if context.path is None:
        return "desktop"
    path = context.path.lower()
    if path.startswith("/api/webhook"):
        return "webhook"
    if path.startswith("/ws/"):
        return "websocket"
    if path.startswith("/api/chat"):
        return "chat"
    return "api"


def _serialize_usage(usage: UsageStats | None) -> dict[str, int | None] | None:
    if usage is None:
        return None
    return {
        "promptTokens": usage.prompt_tokens,
        "completionTokens": usage.completion_tokens,
        "totalTokens": usage.total_tokens,
        "reasoningTokens": usage.reasoning_tokens,
        "cachedInputTokens": usage.cached_input_tokens,
    }


def _normalize_attachments(
    attachments: list[ChatRequestAttachment] | tuple[ChatRequestAttachment, ...],
) -> tuple[ChatRequestAttachment, ...]:
    return tuple(attachments)


def _normalize_context_messages(
    context_messages: list[ChatContextMessage] | tuple[ChatContextMessage, ...],
) -> tuple[ChatContextMessage, ...]:
    return tuple(context_messages)


def _normalize_document_ids(document_ids: list[int] | tuple[int, ...]) -> tuple[int, ...]:
    return tuple(document_ids)


def _emit_request_audit(
    context: RuntimeRequestContext,
    *,
    event: str,
    level: str,
    category: str = "agent",
    data: dict[str, object] | None = None,
    duration_ms: float | None = None,
) -> None:
    payload: dict[str, object] = {
        "path": context.path,
        "user_id": context.user_id,
        "trace_id": context.trace_id,
        "request_id": context.request_id,
        "session_id": context.session_id,
        "device_id": context.device_id,
        "source": context.source,
    }
    if data:
        payload.update(data)
    emit(
        category=category,
        event=event,
        level=level,  # type: ignore[arg-type]
        user_id=context.user_id,
        data=payload,
        duration_ms=duration_ms,
    )


def build_chat_input(
    context: GatewayRequestContext,
    *,
    user_id: int,
    message: str,
    source: str | None = None,
    thread_id: int | None = None,
    attachments: list[ChatRequestAttachment] | tuple[ChatRequestAttachment, ...] = (),
    document_ids: list[int] | tuple[int, ...] = (),
    context_messages: list[ChatContextMessage] | tuple[ChatContextMessage, ...] = (),
    today_context: TodayContext | None = None,
) -> RuntimeChatInput:
    user_message = normalize_document_only_user_message(message, list(document_ids))
    return RuntimeChatInput(
        request=_contract_context(context),
        user_id=user_id,
        user_message=user_message,
        source=source,
        thread_id=thread_id,
        attachments=_normalize_attachments(attachments),
        document_ids=_normalize_document_ids(document_ids),
        context_messages=_normalize_context_messages(context_messages),
        today_context=today_context,
    )


async def execute_chat_non_stream(
    context: GatewayRequestContext,
    db: Session,
    runtime_db: Session,
    *,
    user_id: int,
    message: str,
    source: str | None = None,
    thread_id: int | None = None,
    attachments: list[ChatRequestAttachment] | tuple[ChatRequestAttachment, ...] = (),
    document_ids: list[int] | tuple[int, ...] = (),
    context_messages: list[ChatContextMessage] | tuple[ChatContextMessage, ...] = (),
    today_context: TodayContext | None = None,
) -> RuntimeChatOutput:
    payload = build_chat_input(
        context,
        user_id=user_id,
        message=message,
        source=source,
        thread_id=thread_id,
        attachments=attachments,
        document_ids=document_ids,
        context_messages=context_messages,
        today_context=today_context,
    )
    start = perf_counter()
    _emit_request_audit(
        payload.request,
        event="runtime.chat.start",
        level="info",
        data={"stream": False},
    )
    try:
        result = await asyncio.wait_for(
            run_agent(
                payload.user_message,
                payload.user_id,
                db,
                runtime_db,
                source=payload.source,
                thread_id=payload.thread_id,
                attachments=payload.attachments,
                document_ids=payload.document_ids,
                context_messages=payload.context_messages,
                today_context=payload.today_context,
            ),
            timeout=_CHAT_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        elapsed_ms = (perf_counter() - start) * 1000
        _emit_request_audit(
            payload.request,
            event="runtime.chat.timeout",
            level="warn",
            data={"error": "chat execution timed out"},
            duration_ms=elapsed_ms,
        )
        raise RuntimeInvocationError(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Chat execution timed out.",
        ) from exc
    except AttachmentTooLargeError as exc:
        raise RuntimeInvocationError(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except AttachmentValidationError as exc:
        raise RuntimeInvocationError(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except (LLMConfigError, LLMInvocationError, PromptTemplateError) as exc:
        raise RuntimeInvocationError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise RuntimeInvocationError(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    elapsed_ms = (perf_counter() - start) * 1000
    _emit_request_audit(
        payload.request,
        event="runtime.chat.success",
        level="info",
        data={
            "stream": False,
            "provider": result.provider,
            "model": result.model,
        },
        duration_ms=elapsed_ms,
    )

    return RuntimeChatOutput(
        response=result.response,
        model=result.model,
        provider=result.provider,
        tools_used=list(result.tools_used),
        retrieval=serialize_agent_retrieval(result.retrieval),
        usage=_serialize_usage(summarize_usage(result)),
    )


async def execute_chat_stream(
    context: GatewayRequestContext,
    db: Session,
    runtime_db: Session,
    *,
    user_id: int,
    message: str,
    source: str | None = None,
    thread_id: int | None = None,
    attachments: list[ChatRequestAttachment] | tuple[ChatRequestAttachment, ...] = (),
    document_ids: list[int] | tuple[int, ...] = (),
    context_messages: list[ChatContextMessage] | tuple[ChatContextMessage, ...] = (),
    today_context: TodayContext | None = None,
) -> AsyncGenerator[RuntimeStreamEvent, None]:
    payload = build_chat_input(
        context,
        user_id=user_id,
        message=message,
        source=source,
        thread_id=thread_id,
        attachments=attachments,
        document_ids=document_ids,
        context_messages=context_messages,
        today_context=today_context,
    )
    start = perf_counter()
    _emit_request_audit(
        payload.request,
        event="runtime.chat.stream.start",
        level="info",
        data={"stream": True},
    )
    try:
        ensure_agent_ready()
        async for event in stream_agent(
            payload.user_message,
            payload.user_id,
            db,
            runtime_db,
            source=payload.source,
            thread_id=payload.thread_id,
            attachments=payload.attachments,
            document_ids=payload.document_ids,
            context_messages=payload.context_messages,
            today_context=payload.today_context,
        ):
            yield RuntimeStreamEvent(event=event.event, data=dict(event.data))
    except (LLMConfigError, LLMInvocationError, PromptTemplateError) as exc:
        _emit_request_audit(
            payload.request,
            event="runtime.chat.stream.failed",
            level="warn",
            data={"error": str(exc), "stream": True},
            duration_ms=(perf_counter() - start) * 1000,
        )
        raise RuntimeInvocationError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        _emit_request_audit(
            payload.request,
            event="runtime.chat.stream.failed",
            level="warn",
            data={"error": str(exc), "stream": True},
            duration_ms=(perf_counter() - start) * 1000,
        )
        raise RuntimeInvocationError(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except TimeoutError as exc:
        _emit_request_audit(
            payload.request,
            event="runtime.chat.stream.timeout",
            level="warn",
            data={"error": "chat stream timed out", "stream": True},
            duration_ms=(perf_counter() - start) * 1000,
        )
        raise RuntimeInvocationError(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Chat stream timed out.",
        ) from exc
    except RuntimeInvocationError:
        raise
    except Exception as exc:
        health_emit(
            category="agent",
            event="runtime.chat.stream.exception",
            level="error",
            user_id=payload.request.user_id,
            data={
                "path": payload.request.path,
                "trace_id": payload.request.trace_id,
                "request_id": payload.request.request_id,
                "event": "runtime.chat.stream.exception",
                "error": str(exc),
            },
        )
        _emit_request_audit(
            payload.request,
            event="runtime.chat.stream.exception",
            level="error",
            data={"error": str(exc), "stream": True},
            duration_ms=(perf_counter() - start) * 1000,
        )
        raise RuntimeInvocationError(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred while streaming the chat response.",
        ) from exc

    _emit_request_audit(
        payload.request,
        event="runtime.chat.stream.success",
        level="info",
        data={"stream": True},
        duration_ms=(perf_counter() - start) * 1000,
    )
