from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.config import settings
from anima_server.db import get_db, get_runtime_db
from anima_server.db.session import build_session_factory_for_db
from anima_server.models import MemoryItem
from anima_server.schemas.chat import ChatResetRequest
from anima_server.services.agent import invalidate_agent_runtime_cache
from anima_server.services.agent.embedding_integrity import compute_embedding_checksum
from anima_server.services.agent.memory_store import sync_memory_item_to_retrieval_index
from anima_server.services.agent.persistence import append_message, get_or_create_thread
from anima_server.services.agent.sequencing import reserve_message_sequences
from anima_server.services.agent.text_processing import prepare_embedding_text
from anima_server.services.agent.vector_store import reset_vector_store
from anima_server.services.data_crypto import ef
from anima_server.services.eval_reset import reset_eval_user_state

router = APIRouter(prefix="/api/eval", tags=["eval"])


class EvalTranscriptTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class EvalTranscriptSession(BaseModel):
    date: str | None = None
    turns: list[EvalTranscriptTurn] = Field(min_length=1)


class EvalTranscriptImportRequest(BaseModel):
    userId: int = Field(ge=0)
    sessions: list[EvalTranscriptSession] = Field(min_length=1)
    extractionMode: Literal["llm_pairs", "raw_chunks"] = "llm_pairs"
    embedRawChunks: bool = False


@router.post("/reset")
async def reset_eval_state(
    payload: ChatResetRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Reset benchmark-generated state for a disposable eval account."""

    if not settings.eval_reset_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Eval reset is disabled. Set ANIMA_EVAL_RESET_ENABLED=true "
                "only on a disposable eval server/data directory."
            ),
        )

    require_unlocked_user(request, payload.userId)

    from anima_server.services.agent.consolidation import drain_background_memory_tasks

    await drain_background_memory_tasks()
    deleted = reset_eval_user_state(
        user_id=payload.userId,
        soul_db=db,
        runtime_db=runtime_db,
    )
    reset_vector_store()
    invalidate_agent_runtime_cache()
    return {"status": "reset", "deleted": deleted}


@router.post("/import-transcript")
async def import_eval_transcript(
    payload: EvalTranscriptImportRequest,
    request: Request,
    db: Session = Depends(get_db),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, object]:
    """Import original benchmark transcript turns into eval memory.

    This endpoint is intentionally eval-gated. It avoids replaying benchmark
    history as live chat, which is slow and can drop older facts when background
    memory is disabled for local deterministic eval runs.
    """

    if not settings.eval_reset_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Eval transcript import is disabled. Set ANIMA_EVAL_RESET_ENABLED=true "
                "only on a disposable eval server/data directory."
            ),
        )

    require_unlocked_user(request, payload.userId)

    messages_imported = _persist_imported_transcript(
        payload,
        runtime_db=runtime_db,
    )

    if payload.extractionMode == "raw_chunks":
        raw_result = await _import_raw_transcript_chunks(payload, db=db)
        invalidate_agent_runtime_cache()
        return {
            "status": "imported",
            "extractionMode": payload.extractionMode,
            "embedRawChunks": payload.embedRawChunks,
            "sessionsImported": len(payload.sessions),
            "messagesImported": messages_imported,
            "turnPairsImported": 0,
            "memoryItemsImported": raw_result["memoryItemsImported"],
            "embeddingItemsImported": raw_result["embeddingItemsImported"],
            "errors": raw_result["errors"],
        }

    from anima_server.services.agent.consolidation import run_background_extraction
    from anima_server.services.agent.soul_writer import run_soul_writer

    runtime_db_factory = build_session_factory_for_db(runtime_db)
    turn_pairs_imported = 0
    errors: list[str] = []

    for session in payload.sessions:
        for user_message, assistant_response in _iter_extraction_pairs(session):
            try:
                await run_background_extraction(
                    user_id=payload.userId,
                    user_message=user_message,
                    assistant_response=assistant_response,
                    runtime_db_factory=runtime_db_factory,
                    trigger_soul_writer=False,
                )
                turn_pairs_imported += 1
            except Exception as exc:
                errors.append(str(exc))

    if turn_pairs_imported:
        try:
            await run_soul_writer(payload.userId)
        except Exception as exc:
            errors.append(str(exc))

    invalidate_agent_runtime_cache()
    return {
        "status": "imported",
        "extractionMode": payload.extractionMode,
        "embedRawChunks": False,
        "sessionsImported": len(payload.sessions),
        "messagesImported": messages_imported,
        "turnPairsImported": turn_pairs_imported,
        "memoryItemsImported": 0,
        "embeddingItemsImported": 0,
        "errors": errors,
    }


def _persist_imported_transcript(
    payload: EvalTranscriptImportRequest,
    *,
    runtime_db: Session,
) -> int:
    message_count = sum(len(session.turns) for session in payload.sessions)
    if message_count <= 0:
        return 0

    thread = get_or_create_thread(runtime_db, payload.userId)
    sequence_id = reserve_message_sequences(
        runtime_db,
        thread_id=thread.id,
        count=message_count,
    )

    imported = 0
    for session in payload.sessions:
        for turn in session.turns:
            message = append_message(
                runtime_db,
                thread=thread,
                run_id=None,
                step_id=None,
                sequence_id=sequence_id,
                role=turn.role,
                content_text=_with_session_date(turn.content, session.date),
                source="eval_import",
                is_archived_history=True,
            )
            message.is_in_context = False
            runtime_db.add(message)
            sequence_id += 1
            imported += 1

    runtime_db.commit()
    return imported


def _iter_extraction_pairs(
    session: EvalTranscriptSession,
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for user_message, assistant_response in _iter_raw_pairs(session):
        pairs.append(
            (
                _with_session_date(user_message, session.date) if user_message else "",
                _with_session_date(assistant_response, session.date)
                if assistant_response
                else "",
            )
        )
    return pairs


def _iter_raw_pairs(
    session: EvalTranscriptSession,
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    pending_user: str | None = None

    for turn in session.turns:
        if turn.role == "user":
            if pending_user is not None:
                pairs.append((pending_user, ""))
            pending_user = turn.content
            continue

        if pending_user is None:
            pairs.append(("", turn.content))
        else:
            pairs.append((pending_user, turn.content))
            pending_user = None

    if pending_user is not None:
        pairs.append((pending_user, ""))

    return pairs


async def _import_raw_transcript_chunks(
    payload: EvalTranscriptImportRequest,
    *,
    db: Session,
) -> dict[str, object]:
    chunks = _build_raw_memory_chunks(payload)
    if not chunks:
        return {
            "memoryItemsImported": 0,
            "embeddingItemsImported": 0,
            "errors": [],
        }

    items: list[tuple[MemoryItem, str]] = []
    for chunk in chunks:
        item = MemoryItem(
            user_id=payload.userId,
            content=ef(payload.userId, chunk, table="memory_items", field="content"),
            category="fact",
            importance=4,
            source="eval_import_raw",
        )
        db.add(item)
        items.append((item, chunk))

    db.flush()

    errors: list[str] = []
    if payload.embedRawChunks:
        embeddings = await _generate_raw_chunk_embeddings([chunk for _item, chunk in items], errors)
    else:
        embeddings = [None] * len(items)
    embedded = 0

    for (item, chunk), embedding in zip(items, embeddings, strict=False):
        if embedding is not None:
            item.embedding_json = embedding
            item.embedding_checksum = compute_embedding_checksum(embedding)
            embedded += 1
        sync_memory_item_to_retrieval_index(item)
        if embedding is not None:
            _try_upsert_runtime_embedding(item, chunk, embedding, errors)

    db.commit()
    return {
        "memoryItemsImported": len(items),
        "embeddingItemsImported": embedded,
        "errors": errors,
    }


async def _generate_raw_chunk_embeddings(
    chunks: list[str],
    errors: list[str],
) -> list[list[float] | None]:
    try:
        from anima_server.services.agent.embeddings import generate_embeddings_batch

        return await generate_embeddings_batch(chunks)
    except Exception as exc:
        errors.append(f"embedding generation failed: {exc}")
        return [None] * len(chunks)


def _try_upsert_runtime_embedding(
    item: MemoryItem,
    chunk: str,
    embedding: list[float],
    errors: list[str],
) -> None:
    prepared_text = prepare_embedding_text(chunk) or chunk
    try:
        from anima_server.services.agent.vector_store import upsert_memory

        upsert_memory(
            item.user_id,
            item_id=item.id,
            content=prepared_text,
            embedding=embedding,
            category=item.category,
            importance=item.importance,
        )
    except Exception as exc:
        errors.append(f"runtime embedding upsert failed for memory {item.id}: {exc}")


def _build_raw_memory_chunks(payload: EvalTranscriptImportRequest) -> list[str]:
    chunks: list[str] = []
    for session in payload.sessions:
        for user_message, assistant_response in _iter_raw_pairs(session):
            chunk = _format_raw_memory_chunk(
                user_message=user_message,
                assistant_response=assistant_response,
                date=session.date,
            )
            if chunk:
                chunks.append(chunk)
    return chunks


def _format_raw_memory_chunk(
    *,
    user_message: str,
    assistant_response: str,
    date: str | None,
) -> str:
    parts: list[str] = []
    date_str = (date or "").strip()
    if date_str:
        parts.append(f"Session date: {date_str}")
    user_text = user_message.strip()
    assistant_text = assistant_response.strip()
    if user_text:
        parts.append(f"User: {user_text}")
    if assistant_text:
        parts.append(f"Assistant: {assistant_text}")
    return "\n".join(parts).strip()


def _with_session_date(content: str, date: str | None) -> str:
    stripped = content.strip()
    date_str = (date or "").strip()
    if not date_str:
        return stripped
    return f"[Session date: {date_str}] {stripped}"
