from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from anima_server.api.deps.unlock import require_unlocked_session_async, require_unlocked_user_async
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
    ensure_image_attachments_supported,
    list_agent_history,
    normalize_document_only_user_message,
    reset_agent_thread,
    run_agent,
    stream_agent,
    stream_approve_or_deny,
)
from anima_server.services.agent.attachments import (
    AttachmentReadError,
    AttachmentTooLargeError,
    AttachmentValidationError,
    resolve_corefs_chat_attachment,
    resolve_message_attachment,
    validate_chat_attachment_inputs,
)
from anima_server.services.agent.llm import LLMConfigError, LLMInvocationError
from anima_server.services.agent.memory_store import get_current_focus
from anima_server.services.agent.runtime_types import UsageStats
from anima_server.services.agent.state import (
    extract_stored_pills,
    extract_stored_retrieval,
    serialize_agent_retrieval,
    serialize_public_attachments,
)
from anima_server.services.agent.streaming import summarize_usage
from anima_server.services.agent.system_prompt import PromptTemplateError
from anima_server.services.corefs.asset_authority import (
    CoreFsSourceError,
    open_corefs_byte_source,
)
from anima_server.services.corefs.conversation_authority import (
    canonical_message_api_id,
    conversation_corefs_authority_active,
    get_active_canonical_thread,
    list_canonical_threads,
)
from anima_server.services.corefs.conversation_mutations import (
    ConversationMutationError,
    create_canonical_thread,
)
from anima_server.services.corefs.logical import CoreFsMutationUnavailable
from anima_server.services.corefs.messages import ConversationFormatError
from anima_server.services.corefs.runtime_sealing import RuntimeSealingLocked
from anima_server.services.corefs.sealed_runtime import runtime_index_for_sensitive_write

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class _ClosingStreamingResponse(StreamingResponse):
    """Ensure transport termination closes the route's async body iterator."""

    body_iterator: AsyncGenerator[str, None]

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        spec_version = tuple(
            map(int, scope.get("asgi", {}).get("spec_version", "2.0").split("."))
        )
        if spec_version >= (2, 4):
            await super().__call__(scope, receive, send)
            return

        stream_task = asyncio.create_task(self.stream_response(send))
        disconnect_task = asyncio.create_task(self.listen_for_disconnect(receive))
        try:
            done, _ = await asyncio.wait(
                {stream_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            disconnected = disconnect_task in done and stream_task not in done
            if disconnected:
                stream_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stream_task
            else:
                disconnect_task.cancel()
                await stream_task
        finally:
            for task in (stream_task, disconnect_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stream_task, disconnect_task, return_exceptions=True)

        if self.background is not None:
            await self.background()


@router.post("", response_model=ChatResponse)
async def send_message(
    payload: ChatRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> ChatResponse | StreamingResponse:
    await require_unlocked_user_async(request, payload.userId)
    try:
        runtime_index_for_sensitive_write(runtime_db, user_id=payload.userId)
    except RuntimeSealingLocked as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_runtime_index_unavailable"},
        ) from exc
    message = normalize_document_only_user_message(payload.message, payload.documentIds)

    if not payload.stream:
        try:
            result = await run_agent(
                message, payload.userId, db, runtime_db,
                source=payload.source,
                thread_id=payload.threadId,
                attachments=payload.attachments,
                document_ids=payload.documentIds,
                context_messages=payload.contextMessages,
                today_context=payload.todayContext,
            )
        except AttachmentTooLargeError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=str(exc),
            ) from exc
        except AttachmentValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except (LLMConfigError, LLMInvocationError, PromptTemplateError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return ChatResponse(
            response=result.response,
            model=result.model,
            provider=result.provider,
            toolsUsed=result.tools_used,
            retrieval=serialize_agent_retrieval(result.retrieval),
            usage=_serialize_usage(summarize_usage(result)),
        )

    try:
        ensure_agent_ready()
        ensure_image_attachments_supported(payload.attachments)
        validate_chat_attachment_inputs(payload.attachments)
    except AttachmentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except AttachmentValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (LLMConfigError, LLMInvocationError, PromptTemplateError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            service_stream = stream_agent(
                message, payload.userId, db, runtime_db,
                source=payload.source,
                thread_id=payload.threadId,
                attachments=payload.attachments,
                document_ids=payload.documentIds,
                context_messages=payload.contextMessages,
                today_context=payload.todayContext,
            )
            async with contextlib.aclosing(service_stream):
                async for event in service_stream:
                    if event.event == "thought":
                        continue  # private reasoning, not forwarded to client
                    yield _format_sse_event(event.event, event.data)
        except (LLMConfigError, LLMInvocationError, PromptTemplateError) as exc:
            yield _format_sse_event("error", {"error": str(exc)})
        except ValueError as exc:
            yield _format_sse_event("error", {"error": str(exc), "status": 404})
        except Exception:
            logger.exception("Unexpected error during SSE streaming")
            yield _format_sse_event(
                "error", {"error": "An internal error occurred during streaming."}
            )

    return _ClosingStreamingResponse(
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
    unlock_session = await require_unlocked_user_async(request, userId)
    if conversation_corefs_authority_active(unlock_session):
        # Match the legacy contract: history is the ACTIVE thread's transcript,
        # so reset/clear rotating to a fresh thread empties the chat pane.
        active_view = get_active_canonical_thread(session=unlock_session)
        canonical = list(active_view.messages) if active_view is not None else []
        canonical.sort(key=lambda message: (message.created_at, message.sequence))
        try:
            return [
                ChatHistoryMessage(
                    id=canonical_message_api_id(message),
                    userId=userId,
                    role=message.role,
                    content=message.content,
                    createdAt=datetime.fromisoformat(message.created_at),
                    attachments=[
                        resolve_corefs_chat_attachment(
                            session=unlock_session,
                            object_uri=uri,
                        ).to_public_dict(message_id=canonical_message_api_id(message))
                        for uri in message.attachment_uris
                    ]
                    if message.role == "user"
                    else [],
                    pills=[],
                )
                for message in canonical[-limit:]
            ]
        except (AttachmentReadError, CoreFsSourceError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Canonical chat attachment authority is unavailable.",
            ) from exc
    rows = list_agent_history(userId, runtime_db, limit=limit)
    return [
        ChatHistoryMessage(
            id=row.id,
            userId=userId,
            role="assistant" if row.role == "tool" else row.role,
            content=row.content_text or "",
            createdAt=row.created_at,
            source=getattr(row, "source", None),
            retrieval=extract_stored_retrieval(row.content_json),
            attachments=serialize_public_attachments(
                row.content_json,
                message_id=row.id,
            )
            if row.role == "user"
            else [],
            pills=extract_stored_pills(row.content_json),
        )
        for row in rows
    ]


@router.get("/messages/{message_id}/attachments/{attachment_id}")
async def get_message_attachment(
    message_id: int,
    attachment_id: str,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> Response:
    unlock_session = await require_unlocked_session_async(request)
    if conversation_corefs_authority_active(unlock_session):
        matches = [
            message
            for view in list_canonical_threads(session=unlock_session)
            for message in view.messages
            if canonical_message_api_id(message) == message_id and message.role == "user"
        ]
        object_uri = f"corefs://object/{attachment_id}"
        if len(matches) != 1 or object_uri not in matches[0].attachment_uris:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Attachment not found",
            )
        try:
            source = open_corefs_byte_source(
                session=unlock_session,
                object_uri=object_uri,
                expected_kinds=frozenset({"attachment", "gallery-asset"}),
            )
        except CoreFsSourceError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Canonical chat attachment authority is unavailable.",
            ) from exc
        return StreamingResponse(
            source.iter_chunks(),
            media_type=source.content_type,
        )
    message = runtime_db.get(RuntimeMessage, message_id)
    if (
        message is None
        or message.user_id != unlock_session.user_id
        or message.role != "user"
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    resolved = resolve_message_attachment(
        runtime_db,
        message=message,
        attachment_id=attachment_id,
    )
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    path, mime_type = resolved
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    return FileResponse(path, media_type=mime_type)


@router.delete("/history", response_model=ChatHistoryClearResponse)
async def clear_chat_history(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> ChatHistoryClearResponse:
    unlock_session = await require_unlocked_user_async(request, payload.userId)
    if conversation_corefs_authority_active(unlock_session):
        try:
            create_canonical_thread(session=unlock_session, force_new=True)
        except (
            ConversationFormatError,
            ConversationMutationError,
            CoreFsMutationUnavailable,
            PermissionError,
            ValueError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": str(exc) or "corefs_conversation_mutation_failed"},
            ) from exc
        return ChatHistoryClearResponse(status="cleared")
    await reset_agent_thread(payload.userId, runtime_db, db=db)
    return ChatHistoryClearResponse(status="cleared")


@router.post("/reset", response_model=ChatResetResponse)
async def reset_chat_thread(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> ChatResetResponse:
    unlock_session = await require_unlocked_user_async(request, payload.userId)
    if conversation_corefs_authority_active(unlock_session):
        try:
            create_canonical_thread(session=unlock_session, force_new=True)
        except (
            ConversationFormatError,
            ConversationMutationError,
            CoreFsMutationUnavailable,
            PermissionError,
            ValueError,
        ) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": str(exc) or "corefs_conversation_mutation_failed"},
            ) from exc
        return ChatResetResponse(status="reset")
    await reset_agent_thread(payload.userId, runtime_db, db=db)
    return ChatResetResponse(status="reset")


@router.get("/brief")
async def get_brief(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Quick context brief (static, no LLM). Use /greeting for personalized greetings."""
    await require_unlocked_user_async(request, userId)

    from anima_server.services.agent.proactive import (
        build_static_greeting,
        gather_greeting_context,
    )

    ctx = gather_greeting_context(db, user_id=userId, runtime_db=runtime_db)
    return {
        "message": build_static_greeting(ctx),
        "context": {
            "currentFocus": ctx.current_focus,
            "openTaskCount": ctx.open_task_count,
            "daysSinceLastChat": ctx.days_since_last_chat,
        },
    }


@router.post("/greeting/dream-claim")
async def confirm_greeting_dream_claim(
    request: Request,
    userId: int = Query(ge=0),
    dreamId: int = Query(ge=1),
    claimToken: str = Query(min_length=1, max_length=64),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """IL-015 (PR #135 review, P1): ask whether this dream is still ours to
    voice, immediately before voicing it.

    The client also tracks the claim's expiry locally, but that check
    compares a server timestamp against the DEVICE clock — a skewed clock or
    a delayed render can conclude "still mine" after the claim lapsed and
    another channel took the dream, disclosing the same narrative twice.
    This is the same question answered atomically by the row itself.

    ``{"confirmed": false}`` means the claim is stale (expired and re-taken,
    already acknowledged, or never the caller's): the client must voice the
    dream-free copy instead. On success the claim is RENEWED — not surfaced
    — so a client that dies before painting still loses nothing.
    """
    await require_unlocked_user_async(request, userId)

    from anima_server.services.agent.inner_life.dream_receipt import confirm_claim

    renewed = confirm_claim(db, user_id=userId, dream_id=dreamId, token=claimToken)
    db.commit()
    if renewed is None:
        return {"confirmed": False, "claimToken": None, "expiresAt": None}
    return {
        "confirmed": True,
        "claimToken": renewed.token,
        "expiresAt": renewed.expires_at.isoformat(),
    }


@router.post("/greeting/dream-ack")
async def acknowledge_greeting_dream(
    request: Request,
    userId: int = Query(ge=0),
    dreamId: int = Query(ge=1),
    claimToken: str = Query(min_length=1, max_length=64),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    """IL-015: confirm the client received a dream-bearing greeting.

    Marks the dream ``surfaced`` (the durable "never voice this again"
    flag) and clears its claim. Idempotent, ownership-scoped and CLAIM-scoped:
    acking a dream twice, one belonging to another user, or one whose claim
    has since been superseded returns ``{"acknowledged": false}`` rather than
    erroring — the client acks best-effort and must never be penalised for a
    retry, and a stale ack must not clear a newer greeting's claim.
    """
    await require_unlocked_user_async(request, userId)

    from anima_server.services.agent.inner_life.dream_receipt import (
        acknowledge_dream,
    )

    acknowledged = acknowledge_dream(
        db, user_id=userId, dream_id=dreamId, token=claimToken
    )
    db.commit()
    return {"acknowledged": acknowledged}


@router.get("/greeting")
async def get_greeting(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Generate a personalized greeting using the agent's self-model and context.

    Uses LLM when available, falls back to static greeting otherwise.
    """
    await require_unlocked_user_async(request, userId)

    from anima_server.services.agent.proactive import generate_greeting

    result = await generate_greeting(db, user_id=userId, runtime_db=runtime_db)
    return {
        "message": result.message,
        "llmGenerated": result.llm_generated,
        # IL-010: True when this message voices a CONSUMED ambient dream
        # (already marked surfaced). The client must treat such a greeting as
        # one-shot: display it now, never cache/replay it (PR #130 review).
        "ambientDream": result.context.ambient_dream is not None,
        # IL-015: acknowledge with POST /api/chat/greeting/dream-ack once the
        # client has actually rendered or durably stored this greeting. Until
        # then the claim expires and the dream is offered again — a dropped
        # response no longer silences it.
        "ambientDreamId": result.ambient_dream_id,
        # IL-015 (PR #135 review): when the claim behind this greeting goes
        # stale. A client that STORES the response for a later mount instead
        # of rendering it now must drop the stored copy at this deadline —
        # past it the server may re-offer the same narrative, and replaying
        # the stored greeting would disclose the dream twice.
        # IL-015 (PR #135 review): names the claim generation this greeting
        # holds. The client returns it to POST /chat/greeting/dream-claim
        # before voicing the dream, and to dream-ack afterwards.
        "ambientDreamClaimToken": result.ambient_dream_claim_token,
        "ambientDreamExpiresAt": (
            result.ambient_dream_expires_at.isoformat()
            if result.ambient_dream_expires_at is not None
            else None
        ),
        # The same greeting WITHOUT the dream sentence, for surfaces that
        # forward greeting text into an LLM prompt (the dashboard "explore"
        # handoff). Null when `message` is already dream-free.
        "handoffMessage": result.handoff_message,
        "pills": result.pills,
        "context": {
            "currentFocus": result.context.current_focus,
            "openTaskCount": result.context.open_task_count,
            "overdueTasks": result.context.overdue_task_count,
            "daysSinceLastChat": result.context.days_since_last_chat,
            "upcomingDeadlines": list(result.context.upcoming_deadlines),
        },
    }


@router.get("/reflection")
async def get_reflection(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Generate a personalised daily reflection question."""
    await require_unlocked_user_async(request, userId)

    from anima_server.services.agent.proactive import generate_reflection

    result = await generate_reflection(db, user_id=userId, runtime_db=runtime_db)
    return {
        "question": result.question,
        "llmGenerated": result.llm_generated,
        "curiosityType": result.curiosity_type,
        "sourceEpisodeId": result.source_episode_id,
        "sourceEpisodeDate": result.source_episode_date,
    }


@router.get("/nudges")
async def get_nudges(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, object]]]:
    await require_unlocked_user_async(request, userId)

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


@router.get("/proactive-notice")
async def get_proactive_notice(
    request: Request,
    userId: int = Query(ge=0),
    instruction: str | None = Query(default=None, max_length=500),
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    await require_unlocked_user_async(request, userId)

    from anima_server.services.agent.proactive import generate_proactive_notice

    result = await generate_proactive_notice(
        db,
        user_id=userId,
        instruction=instruction,
        runtime_db=runtime_db,
    )
    if result is None:
        return {"notice": None}

    context_message: dict[str, object] = {
        "role": "assistant",
        "content": result.message,
        "source": result.source,
    }
    if result.pills:
        context_message["pills"] = result.pills

    return {
        "notice": {
            "id": result.id,
            "message": result.message,
            "source": result.source,
            "llmGenerated": result.llm_generated,
            "pills": result.pills,
            "context": {
                "currentFocus": result.context.current_focus,
                "openTaskCount": result.context.open_task_count,
                "overdueTasks": result.context.overdue_task_count,
                "daysSinceLastChat": result.context.days_since_last_chat,
                "upcomingDeadlines": list(result.context.upcoming_deadlines),
            },
            "contextMessages": [context_message],
        }
    }


@router.get("/home")
async def get_home(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    await require_unlocked_user_async(request, userId)

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
                        func.date(RuntimeMessage.created_at) == day,
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
    await require_unlocked_user_async(request, payload.userId)

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
    pairs: list[tuple[list[int], str, str]] = []
    msgs = list(reversed(messages))
    i = 0
    while i < len(msgs) - 1:
        if msgs[i].role == "user" and msgs[i + 1].role == "assistant":
            pairs.append(
                (
                    [int(msgs[i].id), int(msgs[i + 1].id)],
                    msgs[i].content_text or "",
                    msgs[i + 1].content_text or "",
                )
            )
            i += 2
        else:
            i += 1

    rt_factory = build_session_factory_for_db(runtime_db)
    candidates_created = 0
    errors: list[str] = []
    for source_message_ids, user_message, assistant_response in pairs:
        try:
            await run_background_extraction(
                user_id=payload.userId,
                user_message=user_message,
                assistant_response=assistant_response,
                runtime_db_factory=rt_factory,
                source_message_ids=source_message_ids,
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
    """Manually trigger sleep-time maintenance tasks (contradiction scan, profile synthesis, etc.).

    Delegates to the single sleep orchestrator with ``force=True`` so manual
    runs bypass the heat/freshness gates, get ``RuntimeBackgroundTaskRun``
    tracking and consolidation-cursor updates, and stay in sync with the
    automatic per-turn path.  The count-shaped response the Consciousness UI
    expects is rebuilt from the issued task runs' stored results.
    """
    await require_unlocked_user_async(request, payload.userId)

    from anima_server.db.runtime import get_runtime_session_factory
    from anima_server.models.runtime import RuntimeBackgroundTaskRun
    from anima_server.services.agent.sleep_agent import run_sleeptime_agents

    runtime_db_factory = get_runtime_session_factory()
    run_ids = await run_sleeptime_agents(
        user_id=payload.userId,
        user_message="",
        assistant_response="",
        force=True,
        manual=True,
        db_factory=build_session_factory_for_db(db),
        runtime_db_factory=runtime_db_factory,
    )

    # run_ids are "{task_type}:{run_id}" — read each run's stored result for
    # the counts the UI surfaces (tasks that were gated out simply stay 0).
    issued_ids: list[int] = []
    for token in run_ids:
        _, _, raw_id = token.rpartition(":")
        try:
            issued_ids.append(int(raw_id))
        except ValueError:
            continue

    contradictions_found = 0
    contradictions_resolved = 0
    items_merged = 0
    episodes_generated = 0
    embeddings_backfilled = 0
    errors: list[str] = []
    if issued_ids:
        with runtime_db_factory() as rt_db:
            rows = rt_db.scalars(
                select(RuntimeBackgroundTaskRun).where(
                    RuntimeBackgroundTaskRun.id.in_(issued_ids)
                )
            ).all()
        for row in rows:
            result_json = row.result_json if isinstance(row.result_json, dict) else {}
            if row.task_type == "contradiction_scan":
                contradictions_found = int(result_json.get("found", 0) or 0)
                contradictions_resolved = int(result_json.get("resolved", 0) or 0)
            elif row.task_type == "profile_synthesis":
                items_merged = int(result_json.get("merged", 0) or 0)
            elif row.task_type == "episode_gen":
                episodes_generated = 1 if result_json.get("generated") else 0
            elif row.task_type == "embedding_backfill":
                # Report the actual maintenance done (memories embedded + vectors
                # re-synced), not a hard-coded 0.
                embeddings_backfilled = int(
                    result_json.get("backfilled", 0) or 0
                ) + int(result_json.get("resynced", 0) or 0)
            if row.error_message:
                errors.append(f"{row.task_type}: {row.error_message}")

    return {
        "contradictionsFound": contradictions_found,
        "contradictionsResolved": contradictions_resolved,
        "itemsMerged": items_merged,
        "episodesGenerated": episodes_generated,
        "embeddingsBackfilled": embeddings_backfilled,
        "errors": errors,
    }


@router.post("/reflect")
async def trigger_deep_monologue(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Manually trigger a deep inner monologue (full self-model reflection)."""
    await require_unlocked_user_async(request, payload.userId)

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


@router.post("/runs/{run_id}/cancel", response_model=CancelRunResponse)
async def cancel_run(
    run_id: int,
    payload: CancelRunRequest,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> CancelRunResponse:
    """Request cancellation of a running agent turn."""
    await require_unlocked_user_async(request, payload.userId)

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
    await require_unlocked_user_async(request, payload.userId)

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
    await require_unlocked_user_async(request, payload.userId)

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
            service_stream = stream_approve_or_deny(
                run_id,
                payload.userId,
                payload.approved,
                db,
                runtime_db,
                denial_reason=payload.reason,
            )
            async with contextlib.aclosing(service_stream):
                async for event in service_stream:
                    if event.event == "thought":
                        continue  # private reasoning, not forwarded to client
                    yield _format_sse_event(event.event, event.data)

        return _ClosingStreamingResponse(
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
        retrieval=serialize_agent_retrieval(result.retrieval),
        usage=_serialize_usage(summarize_usage(result)),
    )
