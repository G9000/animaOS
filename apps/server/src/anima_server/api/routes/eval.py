from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
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
from anima_server.services.agent.memory_store import (
    invalidate_memory_retrieval_indexes,
    sync_memory_item_to_retrieval_index,
)
from anima_server.services.agent.persistence import append_message, get_or_create_thread
from anima_server.services.agent.provenance import add_memory_item_evidence
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


@dataclass(frozen=True, slots=True)
class _ImportedTranscriptTurn:
    role: Literal["user", "assistant"]
    content: str
    message_id: int
    thread_id: int
    sequence_id: int
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class _ImportedExtractionPair:
    user_message: str
    assistant_response: str
    source_message_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _RawMemoryChunk:
    text: str
    source_message_ids: tuple[int, ...] = ()
    runtime_thread_id: int | None = None
    runtime_message_id: int | None = None
    sequence_id: int | None = None
    speaker: str | None = None
    observed_at: datetime | None = None
    source_created_at: datetime | None = None
    transcript_ref: str | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class _ImportedTranscriptResult:
    messages_imported: int
    extraction_pairs: tuple[_ImportedExtractionPair, ...] = ()
    raw_chunks: tuple[_RawMemoryChunk, ...] = ()


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

    imported = _persist_imported_transcript(
        payload,
        runtime_db=runtime_db,
    )
    messages_imported = imported.messages_imported

    if payload.extractionMode == "raw_chunks":
        raw_result = await _import_raw_transcript_chunks(
            payload,
            db=db,
            chunks=list(imported.raw_chunks),
        )
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

    for pair in imported.extraction_pairs:
        try:
            await run_background_extraction(
                user_id=payload.userId,
                user_message=pair.user_message,
                assistant_response=pair.assistant_response,
                runtime_db_factory=runtime_db_factory,
                trigger_soul_writer=False,
                source_message_ids=list(pair.source_message_ids),
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
) -> _ImportedTranscriptResult:
    message_count = sum(len(session.turns) for session in payload.sessions)
    if message_count <= 0:
        return _ImportedTranscriptResult(messages_imported=0)

    thread = get_or_create_thread(runtime_db, payload.userId)
    sequence_id = reserve_message_sequences(
        runtime_db,
        thread_id=thread.id,
        count=message_count,
    )

    imported = 0
    extraction_pairs: list[_ImportedExtractionPair] = []
    raw_chunks: list[_RawMemoryChunk] = []
    for session in payload.sessions:
        imported_turns: list[_ImportedTranscriptTurn] = []
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
            imported_turns.append(
                _ImportedTranscriptTurn(
                    role=turn.role,
                    content=turn.content,
                    message_id=int(message.id),
                    thread_id=int(message.thread_id),
                    sequence_id=int(message.sequence_id),
                    created_at=message.created_at,
                )
            )
            sequence_id += 1
            imported += 1
        extraction_pairs.extend(
            _build_imported_extraction_pairs(session.date, imported_turns)
        )
        raw_chunks.extend(_build_imported_raw_chunks(session.date, imported_turns))

    runtime_db.commit()
    return _ImportedTranscriptResult(
        messages_imported=imported,
        extraction_pairs=tuple(extraction_pairs),
        raw_chunks=tuple(raw_chunks),
    )


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


def _build_imported_extraction_pairs(
    date: str | None,
    turns: list[_ImportedTranscriptTurn],
) -> list[_ImportedExtractionPair]:
    pairs: list[_ImportedExtractionPair] = []
    pending_user: _ImportedTranscriptTurn | None = None

    for turn in turns:
        if turn.role == "user":
            if pending_user is not None:
                pairs.append(
                    _ImportedExtractionPair(
                        user_message=_with_session_date(pending_user.content, date),
                        assistant_response="",
                        source_message_ids=(pending_user.message_id,),
                    )
                )
            pending_user = turn
            continue

        if pending_user is None:
            pairs.append(
                _ImportedExtractionPair(
                    user_message="",
                    assistant_response=_with_session_date(turn.content, date),
                    source_message_ids=(turn.message_id,),
                )
            )
        else:
            pairs.append(
                _ImportedExtractionPair(
                    user_message=_with_session_date(pending_user.content, date),
                    assistant_response=_with_session_date(turn.content, date),
                    source_message_ids=(pending_user.message_id, turn.message_id),
                )
            )
            pending_user = None

    if pending_user is not None:
        pairs.append(
            _ImportedExtractionPair(
                user_message=_with_session_date(pending_user.content, date),
                assistant_response="",
                source_message_ids=(pending_user.message_id,),
            )
        )

    return pairs


def _build_imported_raw_chunks(
    date: str | None,
    turns: list[_ImportedTranscriptTurn],
) -> list[_RawMemoryChunk]:
    chunks: list[_RawMemoryChunk] = []
    pending_user: _ImportedTranscriptTurn | None = None

    for turn in turns:
        if turn.role == "user":
            if pending_user is not None:
                chunk = _raw_chunk_from_turns(
                    date=date,
                    user_turn=pending_user,
                    assistant_turn=None,
                )
                if chunk is not None:
                    chunks.append(chunk)
            pending_user = turn
            continue

        if pending_user is None:
            chunk = _raw_chunk_from_turns(
                date=date,
                user_turn=None,
                assistant_turn=turn,
            )
        else:
            chunk = _raw_chunk_from_turns(
                date=date,
                user_turn=pending_user,
                assistant_turn=turn,
            )
            pending_user = None
        if chunk is not None:
            chunks.append(chunk)

    if pending_user is not None:
        chunk = _raw_chunk_from_turns(
            date=date,
            user_turn=pending_user,
            assistant_turn=None,
        )
        if chunk is not None:
            chunks.append(chunk)

    return chunks


def _raw_chunk_from_turns(
    *,
    date: str | None,
    user_turn: _ImportedTranscriptTurn | None,
    assistant_turn: _ImportedTranscriptTurn | None,
) -> _RawMemoryChunk | None:
    text = _format_raw_memory_chunk(
        user_message=user_turn.content if user_turn is not None else "",
        assistant_response=assistant_turn.content if assistant_turn is not None else "",
        date=date,
    )
    if not text:
        return None

    source_turns = tuple(turn for turn in (user_turn, assistant_turn) if turn is not None)
    primary = user_turn or (source_turns[0] if source_turns else None)
    observed_at = _parse_session_observed_at(date)
    if observed_at is None and primary is not None:
        observed_at = primary.created_at

    return _RawMemoryChunk(
        text=text,
        source_message_ids=tuple(turn.message_id for turn in source_turns),
        runtime_thread_id=primary.thread_id if primary is not None else None,
        runtime_message_id=primary.message_id if primary is not None else None,
        sequence_id=primary.sequence_id if primary is not None else None,
        speaker=primary.role if primary is not None else "unknown",
        observed_at=observed_at,
        source_created_at=primary.created_at if primary is not None else None,
        transcript_ref=(
            f"eval_import:thread:{primary.thread_id}" if primary is not None else "eval_import"
        ),
        metadata=_raw_chunk_metadata(date),
    )


async def _import_raw_transcript_chunks(
    payload: EvalTranscriptImportRequest,
    *,
    db: Session,
    chunks: list[_RawMemoryChunk] | None = None,
) -> dict[str, object]:
    raw_chunks = chunks if chunks is not None else _build_raw_memory_chunks(payload)
    if not raw_chunks:
        return {
            "memoryItemsImported": 0,
            "embeddingItemsImported": 0,
            "errors": [],
        }

    items: list[tuple[MemoryItem, _RawMemoryChunk]] = []
    for chunk in raw_chunks:
        item = MemoryItem(
            user_id=payload.userId,
            content=ef(payload.userId, chunk.text, table="memory_items", field="content"),
            category="fact",
            importance=4,
            source="eval_import_raw",
        )
        db.add(item)
        items.append((item, chunk))

    db.flush()

    errors: list[str] = []
    if payload.embedRawChunks:
        embeddings = await _generate_raw_chunk_embeddings(
            [chunk.text for _item, chunk in items],
            errors,
        )
    else:
        embeddings = [None] * len(items)
    embedded = 0

    for (item, chunk), embedding in zip(items, embeddings, strict=False):
        add_memory_item_evidence(
            db,
            user_id=payload.userId,
            memory_item_id=item.id,
            evidence_text=chunk.text,
            source_kind="eval_import",
            runtime_thread_id=chunk.runtime_thread_id,
            runtime_message_id=chunk.runtime_message_id,
            runtime_message_ids=list(chunk.source_message_ids) or None,
            transcript_ref=chunk.transcript_ref,
            sequence_id=chunk.sequence_id,
            speaker=chunk.speaker,
            observed_at=chunk.observed_at,
            source_created_at=chunk.source_created_at,
            confidence=1.0,
            extractor="eval_import",
            metadata=chunk.metadata,
        )
        if embedding is not None:
            item.embedding_json = embedding
            item.embedding_checksum = compute_embedding_checksum(embedding)
            embedded += 1
        sync_memory_item_to_retrieval_index(item)
        if embedding is not None:
            _try_upsert_runtime_embedding(item, chunk.text, embedding, errors)

    invalidate_memory_retrieval_indexes(payload.userId)
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


def _build_raw_memory_chunks(payload: EvalTranscriptImportRequest) -> list[_RawMemoryChunk]:
    chunks: list[_RawMemoryChunk] = []
    for session in payload.sessions:
        for user_message, assistant_response in _iter_raw_pairs(session):
            text = _format_raw_memory_chunk(
                user_message=user_message,
                assistant_response=assistant_response,
                date=session.date,
            )
            if text:
                chunks.append(
                    _RawMemoryChunk(
                        text=text,
                        speaker="user" if user_message else "assistant",
                        observed_at=_parse_session_observed_at(session.date),
                        transcript_ref="eval_import",
                        metadata=_raw_chunk_metadata(session.date),
                    )
                )
    return chunks


def _raw_chunk_metadata(date: str | None) -> dict[str, object]:
    metadata: dict[str, object] = {"source": "raw_chunks"}
    date_str = (date or "").strip()
    if date_str:
        metadata["session_date"] = date_str
    return metadata


_SESSION_DATE_RE = re.compile(
    r"(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})"
    r"(?:\D+(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)


def _parse_session_observed_at(date: str | None) -> datetime | None:
    date_str = (date or "").strip()
    if not date_str:
        return None
    match = _SESSION_DATE_RE.search(date_str)
    if match is None:
        return None
    groups = match.groupdict()
    return datetime(
        int(groups["year"]),
        int(groups["month"]),
        int(groups["day"]),
        int(groups["hour"] or 0),
        int(groups["minute"] or 0),
        tzinfo=UTC,
    )


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
