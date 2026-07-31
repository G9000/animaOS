from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from contextlib import suppress
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session, object_session
from sqlalchemy.orm.attributes import set_committed_value

from anima_server.models.corefs_runtime import (
    CoreFSRuntimeBinding,
    CoreFSSealedPayload,
)
from anima_server.services.corefs.indexer import (
    CoreFSProgressiveIndex,
    CoreFSRuntimeLocked,
)
from anima_server.services.corefs.runtime_sealing import (
    RuntimePayloadAAD,
    RuntimeSealingLocked,
    SealedRuntimePayload,
)

if TYPE_CHECKING:
    from anima_server.models.runtime import RuntimeMessage


_UNCHANGED = object()
_PENDING_RUNTIME_INDEX_WRITES = "corefs_pending_runtime_index_writes"
logger = logging.getLogger(__name__)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sealed_lookup_value(
    index: CoreFSProgressiveIndex,
    value: str,
    *,
    max_length: int | None = None,
) -> str:
    lookup = f"sealed:{index.blind_token(value).hex()}"
    if max_length is None:
        return lookup
    if max_length <= len("sealed:"):
        raise ValueError("sealed lookup length cannot hold an opaque token")
    return lookup[:max_length]


def _active_runtime_index(user_id: int) -> CoreFSProgressiveIndex | None:
    from anima_server.services.sessions import unlock_session_store

    return unlock_session_store.get_active_runtime_index(user_id)


def active_runtime_index(user_id: int) -> CoreFSProgressiveIndex | None:
    return _active_runtime_index(user_id)


def active_runtime_indexes(user_id: int) -> tuple[CoreFSProgressiveIndex, ...]:
    from anima_server.services.sessions import unlock_session_store

    indexes = unlock_session_store.get_active_runtime_indexes(user_id)
    if indexes:
        return indexes
    index = _active_runtime_index(user_id)
    return () if index is None else (index,)


def runtime_index_for_sensitive_write(
    runtime_db: Session,
    *,
    user_id: int,
) -> CoreFSProgressiveIndex | None:
    """Return the active sealer, failing closed for a locked CoreFS Runtime."""
    index = active_runtime_index(user_id)
    if index is not None:
        return index
    binding = runtime_db.scalar(select(CoreFSRuntimeBinding.binding_slot).limit(1))
    if binding == 1:
        raise RuntimeSealingLocked(
            "sensitive Runtime writes are unavailable while CoreFS is locked"
        )
    return None


def _current_session_transaction(runtime_db: Session) -> Any:
    return runtime_db.get_nested_transaction() or runtime_db.get_transaction()


def _publish_committed_runtime_index_writes(runtime_db: Session) -> None:
    transaction = _current_session_transaction(runtime_db)
    if transaction is None:
        return
    pending = runtime_db.info.get(_PENDING_RUNTIME_INDEX_WRITES, [])
    for item in tuple(pending):
        if item["transaction"] is not transaction:
            continue
        parent = transaction.parent
        if parent is not None:
            item["transaction"] = parent
            continue
        pending.remove(item)
        item["publish"]()


def _discard_rolled_back_runtime_index_writes(runtime_db: Session) -> None:
    transaction = _current_session_transaction(runtime_db)
    if transaction is None:
        return
    pending = runtime_db.info.get(_PENDING_RUNTIME_INDEX_WRITES, [])
    pending[:] = [item for item in pending if item["transaction"] is not transaction]


def _defer_runtime_index_write_until_root_commit(
    runtime_db: Session,
    publish: Callable[[], None],
) -> None:
    transaction = _current_session_transaction(runtime_db)
    if transaction is None:
        raise RuntimeError("Runtime index publication requires an active transaction")
    if _PENDING_RUNTIME_INDEX_WRITES not in runtime_db.info:
        runtime_db.info[_PENDING_RUNTIME_INDEX_WRITES] = []
        event.listen(
            runtime_db,
            "after_commit",
            _publish_committed_runtime_index_writes,
        )
        event.listen(
            runtime_db,
            "after_rollback",
            _discard_rolled_back_runtime_index_writes,
        )
    runtime_db.info[_PENDING_RUNTIME_INDEX_WRITES].append(
        {
            "transaction": transaction,
            "publish": publish,
        }
    )


def seal_runtime_record(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    row_type: str,
    row_id: int,
    owner_id: int,
    payload: dict[str, object],
) -> None:
    aad = RuntimePayloadAAD(
        row_type=row_type,
        row_id=str(row_id),
        owner_id=str(owner_id),
    )
    plaintext = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sealed = index.seal_runtime_payload(plaintext, aad=aad)
    row_id_hash = _digest(str(row_id))
    stored = runtime_db.scalar(
        select(CoreFSSealedPayload).where(
            CoreFSSealedPayload.core_id == index.core_id,
            CoreFSSealedPayload.local_instance_id == index.local_instance_id,
            CoreFSSealedPayload.row_type == row_type,
            CoreFSSealedPayload.row_id_hash == row_id_hash,
        )
    )
    if stored is None:
        stored = CoreFSSealedPayload(
            core_id=index.core_id,
            local_instance_id=index.local_instance_id,
            row_type=row_type,
            row_id_hash=row_id_hash,
            owner_id_hash=_digest(str(owner_id)),
            key_version=sealed.version,
            nonce=sealed.nonce,
            ciphertext=sealed.ciphertext,
            aad_digest=hashlib.sha256(aad.encode()).hexdigest(),
        )
        runtime_db.add(stored)
    else:
        stored.owner_id_hash = _digest(str(owner_id))
        stored.key_version = sealed.version
        stored.nonce = sealed.nonce
        stored.ciphertext = sealed.ciphertext
        stored.aad_digest = hashlib.sha256(aad.encode()).hexdigest()
    runtime_db.flush()


def seal_runtime_fields(
    runtime_db: Session,
    *,
    row: Any,
    row_type: str,
    owner_id: int,
    payload: dict[str, object],
    placeholders: dict[str, object],
) -> None:
    """Persist private ORM fields as placeholders plus an unlock-sealed payload."""
    index = runtime_index_for_sensitive_write(
        runtime_db,
        user_id=owner_id,
    )
    if index is None:
        for field, value in payload.items():
            setattr(row, field, value)
        runtime_db.add(row)
        runtime_db.flush([row])
        return
    runtime_db.add(row)
    row_id = getattr(row, "id", None)
    if not isinstance(row_id, int):
        for field, placeholder in placeholders.items():
            setattr(row, field, placeholder)
        runtime_db.flush([row])
        row_id = getattr(row, "id", None)
    if not isinstance(row_id, int):
        raise ValueError("sealed Runtime row requires an integer primary key")
    for field, placeholder in placeholders.items():
        setattr(row, field, placeholder)
    runtime_db.flush([row])
    seal_runtime_record(
        runtime_db,
        index=index,
        row_type=row_type,
        row_id=row_id,
        owner_id=owner_id,
        payload=payload,
    )
    for field, value in payload.items():
        if hasattr(type(row), field):
            set_committed_value(row, field, value)


def runtime_private_lookup_value(
    runtime_db: Session,
    *,
    owner_id: int,
    value: str,
    max_length: int | None = None,
) -> str:
    """Return a queryable opaque projection for a private Runtime identifier."""
    index = runtime_index_for_sensitive_write(
        runtime_db,
        user_id=owner_id,
    )
    return (
        value
        if index is None
        else _sealed_lookup_value(index, value, max_length=max_length)
    )


def persist_runtime_embedding(
    runtime_db: Session,
    *,
    row: Any,
    owner_id: int,
    embedding: Sequence[float],
    content: str,
) -> None:
    """Keep CoreFS-bound vectors in unlock memory while persisting safe metadata."""
    from anima_server.services.agent.embedding_integrity import (
        compute_embedding_checksum,
    )

    vector = tuple(float(value) for value in embedding)
    row.embedding = None
    row.embedding_checksum = None
    index = runtime_index_for_sensitive_write(
        runtime_db,
        user_id=owner_id,
    )
    embedding_fingerprint = (
        None if index is None else index.runtime_embedding_fingerprint()
    )
    content_preview = content[:200]
    if index is None:
        row.embedding = list(vector)
        row.embedding_checksum = compute_embedding_checksum(vector)
    seal_runtime_fields(
        runtime_db,
        row=row,
        row_type="runtime_embedding",
        owner_id=owner_id,
        payload=(
            {"content_preview": content_preview}
            if index is None
            else {
                "content_preview": content_preview,
                "embedding_content": content,
            }
        ),
        placeholders={"content_preview": ""},
    )
    if index is not None:
        source_type = str(row.source_type)
        source_id = int(row.source_id)
        category = str(row.category)
        importance = int(row.importance)

        def publish() -> None:
            for live_index in active_runtime_indexes(owner_id):
                with suppress(CoreFSRuntimeLocked, ValueError):
                    live_index.upsert_runtime_embedding(
                        source_type=source_type,
                        source_id=source_id,
                        vector=vector,
                        content=content_preview,
                        category=category,
                        importance=importance,
                        embedding_fingerprint=embedding_fingerprint,
                    )

        _defer_runtime_index_write_until_root_commit(runtime_db, publish)


def load_runtime_embedding_vector(
    runtime_db: Session,
    *,
    owner_id: int,
    source_type: str,
    source_id: int,
    persisted_embedding: Sequence[float] | None,
) -> tuple[float, ...] | None:
    """Load an embedding from unlock memory, falling back only for unbound Runtime."""
    index = runtime_index_for_sensitive_write(
        runtime_db,
        user_id=owner_id,
    )
    if index is not None:
        return index.runtime_embedding_vector(
            source_type=source_type,
            source_id=source_id,
        )
    if persisted_embedding is None:
        return None
    return tuple(float(value) for value in persisted_embedding)


def convert_legacy_runtime_rows(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    user_id: int,
    memory_dek: bytes | None = None,
) -> int:
    """Seal and scrub legacy plaintext Runtime rows for one unlocked owner.

    Alembic cannot perform this conversion because the sealing key only exists
    inside an unlock session. Each row is sealed and scrubbed in the caller's
    transaction, and an existing sealed payload makes the pass idempotent.
    """
    from anima_server.models.pending_memory_op import PendingMemoryOp
    from anima_server.models.runtime import (
        RuntimeBackgroundTaskRun,
        RuntimeDocument,
        RuntimeDocumentChunk,
        RuntimeImageAnnotation,
        RuntimeImageAsset,
        RuntimeKnowledgeBundleRun,
        RuntimeKnowledgeConcept,
        RuntimeKnowledgeConceptSource,
        RuntimeKnowledgeLink,
        RuntimeMessage,
        RuntimeRun,
        RuntimeSource,
        RuntimeSourceArtifact,
        RuntimeSourceSpan,
        RuntimeStep,
        RuntimeThread,
        RuntimeWorkflowCheckpoint,
        RuntimeWorkflowRun,
    )
    from anima_server.models.runtime_memory import (
        MemoryCandidate,
        MemoryExtractionFailure,
        ProfileUpdateCandidate,
        RuntimeSessionNote,
    )
    from anima_server.services.ingestion.compiler import KNOWLEDGE_LINK_TYPES

    specifications = (
        (
            RuntimeDocument.__table__,
            "runtime_document",
            {
                "filename": "filename",
                "mime_type": "mime_type",
                "storage_path": "storage_path",
                "metadata_json": "metadata_json",
            },
            {"filename": "", "mime_type": "", "storage_path": "", "metadata_json": None},
        ),
        (
            RuntimeImageAsset.__table__,
            "runtime_image_asset",
            {
                "filename": "filename",
                "mime_type": "mime_type",
                "storage_path": "storage_path",
                "metadata_json": "metadata_json",
            },
            {"filename": None, "mime_type": "", "storage_path": "", "metadata_json": None},
        ),
        (
            RuntimeSource.__table__,
            "runtime_source",
            {
                "source_uri": "source_uri",
                "title": "title",
                "media_type": "media_type",
                "metadata_json": "metadata_json",
            },
            {"source_uri": "", "title": None, "media_type": None, "metadata_json": None},
        ),
        (
            RuntimeMessage.__table__,
            "runtime_message",
            {
                "content_text": "content_text",
                "content_json": "content_json",
                "tool_args_json": "tool_args_json",
            },
            {"content_text": None, "content_json": None, "tool_args_json": None},
        ),
        (
            RuntimeRun.__table__,
            "runtime_run",
            {"error_text": "error_text"},
            {"error_text": None},
        ),
        (
            RuntimeBackgroundTaskRun.__table__,
            "runtime_background_task_run",
            {
                "result_json": "result_json",
                "error_message": "error_message",
            },
            {"result_json": None, "error_message": None},
        ),
        (
            RuntimeDocumentChunk.__table__,
            "runtime_document_chunk",
            {
                "content_text": "content_text",
                "section_title": "section_title",
                "metadata_json": "metadata_json",
            },
            {"content_text": "", "section_title": None, "metadata_json": None},
        ),
        (
            RuntimeImageAnnotation.__table__,
            "runtime_image_annotation",
            {"content_text": "content_text"},
            {"content_text": ""},
        ),
        (
            RuntimeSourceArtifact.__table__,
            "runtime_source_artifact",
            {"content_text": "content_text", "metadata_json": "metadata_json"},
            {"content_text": None, "metadata_json": None},
        ),
        (
            RuntimeSourceSpan.__table__,
            "runtime_source_span",
            {"content_text": "content_text", "metadata_json": "metadata_json"},
            {"content_text": "", "metadata_json": None},
        ),
        (
            MemoryCandidate.__table__,
            "memory_candidate",
            {
                "content": "content",
                "tags": "tags_json",
                "salience": "salience_json",
                "last_error": "last_error",
            },
            {
                "content": "",
                "tags_json": None,
                "salience_json": None,
                "last_error": None,
            },
        ),
        (
            MemoryExtractionFailure.__table__,
            "memory_extraction_failure",
            {
                "user_message_preview": "user_message_preview",
                "assistant_response_preview": "assistant_response_preview",
                "failure_reason": "failure_reason",
            },
            {
                "user_message_preview": None,
                "assistant_response_preview": None,
                "failure_reason": "",
            },
        ),
        (
            ProfileUpdateCandidate.__table__,
            "profile_update_candidate",
            {
                "key": "key",
                "value": "value",
                "evidence_text": "evidence_text",
                "last_error": "last_error",
            },
            {"key": "", "value": "", "evidence_text": None, "last_error": None},
        ),
        (
            RuntimeSessionNote.__table__,
            "runtime_session_note",
            {"key": "key", "value": "value"},
            {"key": "", "value": ""},
        ),
        (
            PendingMemoryOp.__table__,
            "pending_memory_op",
            {
                "content": "content",
                "old_content": "old_content",
                "failure_reason": "failure_reason",
            },
            {"content": "", "old_content": None, "failure_reason": None},
        ),
        (
            RuntimeWorkflowRun.__table__,
            "runtime_workflow_run",
            {
                "input_json": "input_json",
                "result_json": "result_json",
                "error_json": "error_json",
            },
            {"input_json": None, "result_json": None, "error_json": None},
        ),
        (
            RuntimeKnowledgeBundleRun.__table__,
            "runtime_knowledge_bundle_run",
            {
                "input_json": "input_json",
                "result_json": "result_json",
                "error_json": "error_json",
            },
            {"input_json": None, "result_json": None, "error_json": None},
        ),
        (
            RuntimeThread.__table__,
            "runtime_thread",
            {"title": "title"},
            {"title": None},
        ),
        (
            RuntimeKnowledgeConcept.__table__,
            "runtime_knowledge_concept",
            {
                "concept_type": "concept_type",
                "slug": "slug",
                "title": "title",
                "description": "description",
                "body_markdown": "body_markdown",
                "frontmatter_json": "frontmatter_json",
            },
            {
                "concept_type": "",
                "slug": "",
                "title": "",
                "description": None,
                "body_markdown": "",
                "frontmatter_json": {},
            },
        ),
        (
            RuntimeKnowledgeConceptSource.__table__,
            "runtime_knowledge_concept_source",
            {"quote_text": "quote_text"},
            {"quote_text": None},
        ),
    )

    converted = _convert_legacy_runtime_embeddings(
        runtime_db,
        index=index,
        user_id=user_id,
        memory_dek=memory_dek,
    )
    link_table = RuntimeKnowledgeLink.__table__
    legacy_related_links = list(
        runtime_db.execute(
            select(
                link_table.c.id,
                link_table.c.source_concept_id,
                link_table.c.target_concept_id,
            ).where(
                link_table.c.user_id == user_id,
                link_table.c.link_type == "relates_to",
            )
        )
        .mappings()
    )
    for legacy_link in legacy_related_links:
        canonical_link_id = runtime_db.scalar(
            select(link_table.c.id).where(
                link_table.c.user_id == user_id,
                link_table.c.source_concept_id
                == legacy_link["source_concept_id"],
                link_table.c.target_concept_id
                == legacy_link["target_concept_id"],
                link_table.c.link_type == "related",
            )
        )
        if canonical_link_id is None:
            runtime_db.execute(
                link_table.update()
                .where(link_table.c.id == legacy_link["id"])
                .values(link_type="related")
            )
        else:
            runtime_db.execute(
                delete(link_table).where(link_table.c.id == legacy_link["id"])
            )
        converted += 1
    discarded_links = runtime_db.execute(
        delete(RuntimeKnowledgeLink).where(
            RuntimeKnowledgeLink.user_id == user_id,
            RuntimeKnowledgeLink.link_type.not_in(tuple(KNOWLEDGE_LINK_TYPES)),
        )
    ).rowcount
    converted += max(int(discarded_links or 0), 0)
    for table, row_type, payload_columns, placeholders in specifications:
        statement = select(
            table.c.id.label("_row_id"),
            table.c.user_id.label("_owner_id"),
            *(table.c[column].label(column) for column in payload_columns.values()),
        ).where(table.c.user_id == user_id)
        converted += _convert_legacy_statement(
            runtime_db,
            index=index,
            table=table,
            row_type=row_type,
            statement=statement,
            payload_columns=payload_columns,
            placeholders=placeholders,
        )

    step_table = RuntimeStep.__table__
    thread_table = RuntimeThread.__table__
    converted += _convert_legacy_statement(
        runtime_db,
        index=index,
        table=step_table,
        row_type="runtime_step",
        statement=(
            select(
                step_table.c.id.label("_row_id"),
                thread_table.c.user_id.label("_owner_id"),
                step_table.c.request_json,
                step_table.c.response_json,
                step_table.c.tool_calls_json,
            )
            .join(thread_table, thread_table.c.id == step_table.c.thread_id)
            .where(thread_table.c.user_id == user_id)
        ),
        payload_columns={
            "request_json": "request_json",
            "response_json": "response_json",
            "tool_calls_json": "tool_calls_json",
        },
        placeholders={
            "request_json": {},
            "response_json": {},
            "tool_calls_json": None,
        },
    )
    checkpoint_table = RuntimeWorkflowCheckpoint.__table__
    workflow_table = RuntimeWorkflowRun.__table__
    converted += _convert_legacy_statement(
        runtime_db,
        index=index,
        table=checkpoint_table,
        row_type="runtime_workflow_checkpoint",
        statement=(
            select(
                checkpoint_table.c.id.label("_row_id"),
                workflow_table.c.user_id.label("_owner_id"),
                checkpoint_table.c.input_json,
                checkpoint_table.c.output_json,
                checkpoint_table.c.error_json,
            )
            .join(
                workflow_table,
                workflow_table.c.id == checkpoint_table.c.workflow_run_id,
            )
            .where(workflow_table.c.user_id == user_id)
        ),
        payload_columns={
            "input_json": "input_json",
            "output_json": "output_json",
            "error_json": "error_json",
        },
        placeholders={"input_json": None, "output_json": None, "error_json": None},
    )
    runtime_db.flush()
    return converted


def _convert_legacy_runtime_embeddings(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    user_id: int,
    memory_dek: bytes | None,
) -> int:
    """Move legacy Runtime vectors into unlock memory and scrub PostgreSQL."""
    from anima_server.models.runtime_embedding import RuntimeEmbedding

    table = RuntimeEmbedding.__table__
    statement = select(
        table.c.id,
        table.c.user_id,
        table.c.source_type,
        table.c.source_id,
        table.c.content_preview,
        table.c.category,
        table.c.importance,
        table.c.embedding,
        table.c.embedding_checksum,
    ).where(table.c.user_id == user_id)
    converted = 0
    for row in runtime_db.execute(statement).mappings():
        row_id = int(row["id"])
        owner_id = int(row["user_id"])
        payload = _load_runtime_record_with_index(
            runtime_db,
            index=index,
            row_type="runtime_embedding",
            row_id=row_id,
            owner_id=owner_id,
        )
        created_payload = payload is None
        preview = str(
            row["content_preview"] if payload is None else payload.get("content_preview", "")
        )
        if payload is None:
            embedding_content = _legacy_runtime_embedding_content(
                runtime_db,
                owner_id=owner_id,
                source_type=str(row["source_type"]),
                source_id=int(row["source_id"]),
                memory_dek=memory_dek,
            )
            payload = {
                "content_preview": preview,
                "embedding_content": embedding_content or preview,
            }
            seal_runtime_record(
                runtime_db,
                index=index,
                row_type="runtime_embedding",
                row_id=row_id,
                owner_id=owner_id,
                payload=payload,
            )

        stored_vector = row["embedding"]
        if stored_vector is not None:
            index.upsert_runtime_embedding(
                source_type=str(row["source_type"]),
                source_id=int(row["source_id"]),
                vector=tuple(float(value) for value in stored_vector),
                content=preview,
                category=str(row["category"]),
                importance=int(row["importance"]),
            )

        has_plaintext = bool(row["content_preview"])
        has_persisted_vector = stored_vector is not None or row["embedding_checksum"] is not None
        if created_payload or has_plaintext or has_persisted_vector:
            runtime_db.execute(
                table.update()
                .where(table.c.id == row_id)
                .values(
                    content_preview="",
                    embedding=None,
                    embedding_checksum=None,
                )
            )
            converted += 1
    return converted


def _legacy_runtime_embedding_content(
    runtime_db: Session,
    *,
    owner_id: int,
    source_type: str,
    source_id: int,
    memory_dek: bytes | None,
) -> str | None:
    """Recover the original embedding input before legacy source rows are scrubbed."""
    from anima_server.models.runtime import (
        RuntimeDocumentChunk,
        RuntimeImageAnnotation,
        RuntimeKnowledgeConcept,
        RuntimeSourceSpan,
    )

    if source_type == "document_chunk":
        row = runtime_db.execute(
            select(
                RuntimeDocumentChunk.__table__.c.content_text,
                RuntimeDocumentChunk.__table__.c.section_title,
                RuntimeDocumentChunk.__table__.c.metadata_json,
            ).where(
                RuntimeDocumentChunk.__table__.c.id == source_id,
                RuntimeDocumentChunk.__table__.c.user_id == owner_id,
            )
        ).one_or_none()
        if row is not None:
            from anima_server.services.documents.contextual import chunk_index_text

            return chunk_index_text(
                SimpleNamespace(
                    content_text=str(row.content_text),
                    section_title=row.section_title,
                    metadata_json=row.metadata_json,
                )
            )
    elif source_type == "image_annotation":
        return runtime_db.scalar(
            select(RuntimeImageAnnotation.__table__.c.content_text).where(
                RuntimeImageAnnotation.__table__.c.id == source_id,
                RuntimeImageAnnotation.__table__.c.user_id == owner_id,
            )
        )
    elif source_type == "source_span":
        return runtime_db.scalar(
            select(RuntimeSourceSpan.__table__.c.content_text).where(
                RuntimeSourceSpan.__table__.c.id == source_id,
                RuntimeSourceSpan.__table__.c.user_id == owner_id,
            )
        )
    elif source_type == "knowledge_concept":
        row = runtime_db.execute(
            select(
                RuntimeKnowledgeConcept.__table__.c.title,
                RuntimeKnowledgeConcept.__table__.c.description,
                RuntimeKnowledgeConcept.__table__.c.body_markdown,
            ).where(
                RuntimeKnowledgeConcept.__table__.c.id == source_id,
                RuntimeKnowledgeConcept.__table__.c.user_id == owner_id,
            )
        ).one_or_none()
        if row is not None:
            return "\n\n".join(
                value
                for value in (str(row.title), row.description, str(row.body_markdown))
                if isinstance(value, str) and value.strip()
            )
    elif source_type == "memory_item":
        try:
            from anima_server.db.session import get_user_session_factory
            from anima_server.models import MemoryItem
            from anima_server.services.crypto import decrypt_text_with_dek

            with get_user_session_factory(owner_id)() as soul_db:
                memory = soul_db.get(MemoryItem, source_id)
                if memory is not None and memory.user_id == owner_id:
                    if memory_dek is None:
                        return memory.content
                    return decrypt_text_with_dek(
                        memory.content,
                        memory_dek,
                        aad=f"memory_items:{owner_id}:content".encode(),
                    )
        except Exception:
            logger.exception(
                "Unable to recover legacy memory embedding input for %s:%s",
                owner_id,
                source_id,
            )
    return None


def rebuild_runtime_embeddings(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    user_id: int,
    embedder: Callable[[str], Sequence[float]],
) -> int:
    """Regenerate unlock-only vectors from sealed embedding inputs."""
    from anima_server.models.runtime_embedding import RuntimeEmbedding

    fingerprint_value = getattr(embedder, "corefs_embedding_fingerprint", None)
    embedding_fingerprint = (
        fingerprint_value if isinstance(fingerprint_value, str) and fingerprint_value else None
    )
    table = RuntimeEmbedding.__table__
    statement = select(
        table.c.id,
        table.c.user_id,
        table.c.source_type,
        table.c.source_id,
        table.c.category,
        table.c.importance,
    ).where(table.c.user_id == user_id)
    rows = list(runtime_db.execute(statement).mappings())
    index.begin_runtime_embedding_rebuild(
        embedding_fingerprint=embedding_fingerprint,
        expected_count=len(rows),
    )
    rebuilt = 0
    for row in rows:
        source_type = str(row["source_type"])
        source_id = int(row["source_id"])
        if (
            index.runtime_embedding_vector(
                source_type=source_type,
                source_id=source_id,
            )
            is not None
        ):
            continue
        payload = _load_runtime_record_with_index(
            runtime_db,
            index=index,
            row_type="runtime_embedding",
            row_id=int(row["id"]),
            owner_id=int(row["user_id"]),
        )
        if payload is None:
            index.mark_runtime_embedding_failure(
                source_type=source_type,
                source_id=source_id,
            )
            continue
        embedding_content = payload.get(
            "embedding_content",
            payload.get("content_preview"),
        )
        if not isinstance(embedding_content, str) or not embedding_content:
            index.mark_runtime_embedding_failure(
                source_type=source_type,
                source_id=source_id,
            )
            continue
        try:
            vector = tuple(float(value) for value in embedder(embedding_content))
            index.upsert_runtime_embedding(
                source_type=source_type,
                source_id=source_id,
                vector=vector,
                content=str(payload.get("content_preview", embedding_content[:200])),
                category=str(row["category"]),
                importance=int(row["importance"]),
                embedding_fingerprint=embedding_fingerprint,
            )
        except (TypeError, ValueError):
            index.mark_runtime_embedding_failure(
                source_type=source_type,
                source_id=source_id,
            )
            logger.exception(
                "Failed to rebuild Runtime embedding %s:%s",
                source_type,
                source_id,
            )
            continue
        rebuilt += 1
    if index.snapshot().catalog_generation is not None:
        index.publish_runtime_embedding_readiness()
    return rebuilt


def _convert_legacy_statement(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    table: Any,
    row_type: str,
    statement: Any,
    payload_columns: dict[str, str],
    placeholders: dict[str, object],
) -> int:
    converted = 0
    for row in runtime_db.execute(statement).mappings():
        row_id = int(row["_row_id"])
        owner_id = int(row["_owner_id"])
        already_sealed = runtime_db.scalar(
            select(CoreFSSealedPayload.id).where(
                CoreFSSealedPayload.core_id == index.core_id,
                CoreFSSealedPayload.local_instance_id == index.local_instance_id,
                CoreFSSealedPayload.row_type == row_type,
                CoreFSSealedPayload.row_id_hash == _digest(str(row_id)),
            )
        )
        row_payload = {
            payload_name: row[column_name]
            for payload_name, column_name in payload_columns.items()
        }
        if already_sealed is not None:
            existing_payload = _load_runtime_record_with_index(
                runtime_db,
                index=index,
                row_type=row_type,
                row_id=row_id,
                owner_id=owner_id,
            )
            if existing_payload is None:
                raise ValueError("sealed Runtime payload is missing")
            missing_fields = row_payload.keys() - existing_payload.keys()
            if not missing_fields:
                continue
            payload = dict(existing_payload)
            payload.update(
                {
                    field: row_payload[field]
                    for field in missing_fields
                }
            )
        else:
            payload = row_payload
        seal_runtime_record(
            runtime_db,
            index=index,
            row_type=row_type,
            row_id=row_id,
            owner_id=owner_id,
            payload=payload,
        )
        scrubbed = dict(placeholders)
        if row_type == "runtime_document_chunk":
            content_text = payload["content_text"]
            if not isinstance(content_text, str):
                raise ValueError("legacy Runtime document chunk text is invalid")
            scrubbed["content_char_count"] = len(content_text)
        elif row_type == "runtime_source":
            source_uri = payload["source_uri"]
            if not isinstance(source_uri, str):
                raise ValueError("legacy Runtime source URI is invalid")
            scrubbed["source_uri"] = _sealed_lookup_value(index, source_uri)
        elif row_type == "runtime_knowledge_concept":
            concept_type = payload["concept_type"]
            slug = payload["slug"]
            if not isinstance(concept_type, str):
                raise ValueError("legacy Runtime knowledge concept type is invalid")
            if not isinstance(slug, str):
                raise ValueError("legacy Runtime knowledge concept slug is invalid")
            scrubbed["concept_type"] = _sealed_lookup_value(
                index,
                concept_type,
                max_length=48,
            )
            scrubbed["slug"] = _sealed_lookup_value(index, slug)
        runtime_db.execute(table.update().where(table.c.id == row_id).values(**scrubbed))
        converted += 1
    return converted


def reseal_runtime_message(
    runtime_db: Session,
    message: RuntimeMessage,
    *,
    content_text: str | None | object = _UNCHANGED,
    content_json: dict[str, object] | None | object = _UNCHANGED,
    tool_args_json: dict[str, object] | None | object = _UNCHANGED,
) -> None:
    """Persist a Runtime message mutation without exposing its private fields."""
    next_content_text = message.content_text if content_text is _UNCHANGED else content_text
    next_content_json = message.content_json if content_json is _UNCHANGED else content_json
    next_tool_args_json = message.tool_args_json if tool_args_json is _UNCHANGED else tool_args_json
    runtime_index = runtime_index_for_sensitive_write(
        runtime_db,
        user_id=int(message.user_id),
    )
    if runtime_index is None:
        message.content_text = next_content_text
        message.content_json = next_content_json
        message.tool_args_json = next_tool_args_json
        return

    # These assignments happen before any sealing query can autoflush. Existing
    # sealed rows already contain these placeholders; legacy plaintext rows are
    # scrubbed as they are first mutated under an active CoreFS binding.
    message.content_text = None
    message.content_json = None
    message.tool_args_json = None
    runtime_db.flush([message])
    seal_runtime_record(
        runtime_db,
        index=runtime_index,
        row_type="runtime_message",
        row_id=int(message.id),
        owner_id=int(message.user_id),
        payload={
            "content_text": next_content_text,
            "content_json": next_content_json,
            "tool_args_json": next_tool_args_json,
        },
    )
    set_committed_value(message, "content_text", next_content_text)
    set_committed_value(message, "content_json", next_content_json)
    set_committed_value(message, "tool_args_json", next_tool_args_json)


def reseal_memory_extraction_failure(
    runtime_db: Session,
    failure: Any,
    *,
    failure_reason: str,
) -> None:
    """Replace a retry failure reason without exposing its sealed previews."""
    next_reason = failure_reason[:2000]
    runtime_index = runtime_index_for_sensitive_write(
        runtime_db,
        user_id=int(failure.user_id),
    )
    if runtime_index is None:
        failure.failure_reason = next_reason
        return

    user_preview = failure.user_message_preview
    assistant_preview = failure.assistant_response_preview
    failure.user_message_preview = None
    failure.assistant_response_preview = None
    failure.failure_reason = ""
    runtime_db.flush([failure])
    seal_runtime_record(
        runtime_db,
        index=runtime_index,
        row_type="memory_extraction_failure",
        row_id=int(failure.id),
        owner_id=int(failure.user_id),
        payload={
            "user_message_preview": user_preview,
            "assistant_response_preview": assistant_preview,
            "failure_reason": next_reason,
        },
    )
    set_committed_value(failure, "user_message_preview", user_preview)
    set_committed_value(
        failure,
        "assistant_response_preview",
        assistant_preview,
    )
    set_committed_value(failure, "failure_reason", next_reason)


def reseal_memory_candidate_error(
    runtime_db: Session,
    candidate: Any,
    *,
    last_error: str | None,
) -> None:
    """Replace a candidate error without exposing its sealed private fields."""
    runtime_index = runtime_index_for_sensitive_write(
        runtime_db,
        user_id=int(candidate.user_id),
    )
    if runtime_index is None:
        candidate.last_error = last_error
        return

    content = candidate.content
    tags = candidate.tags_json
    salience = candidate.salience_json
    candidate.content = ""
    candidate.tags_json = None
    candidate.salience_json = None
    candidate.last_error = None
    runtime_db.flush([candidate])
    seal_runtime_record(
        runtime_db,
        index=runtime_index,
        row_type="memory_candidate",
        row_id=int(candidate.id),
        owner_id=int(candidate.user_id),
        payload={
            "content": content,
            "tags": tags,
            "salience": salience,
            "last_error": last_error,
        },
    )
    set_committed_value(candidate, "content", content)
    set_committed_value(candidate, "tags_json", tags)
    set_committed_value(candidate, "salience_json", salience)
    set_committed_value(candidate, "last_error", last_error)


def reseal_profile_update_candidate_error(
    runtime_db: Session,
    candidate: Any,
    *,
    last_error: str | None,
) -> None:
    """Replace a profile-candidate error within its sealed payload."""
    seal_runtime_fields(
        runtime_db,
        row=candidate,
        row_type="profile_update_candidate",
        owner_id=int(candidate.user_id),
        payload={
            "key": candidate.key,
            "value": candidate.value,
            "evidence_text": candidate.evidence_text,
            "last_error": last_error,
        },
        placeholders={
            "key": "",
            "value": "",
            "evidence_text": None,
            "last_error": None,
        },
    )


def reseal_pending_memory_op_error(
    runtime_db: Session,
    op: Any,
    *,
    failure_reason: str | None,
) -> None:
    """Replace a pending-op failure within its sealed payload."""
    seal_runtime_fields(
        runtime_db,
        row=op,
        row_type="pending_memory_op",
        owner_id=int(op.user_id),
        payload={
            "content": op.content,
            "old_content": op.old_content,
            "failure_reason": failure_reason,
        },
        placeholders={
            "content": "",
            "old_content": None,
            "failure_reason": None,
        },
    )


def delete_sealed_runtime_records(
    runtime_db: Session,
    *,
    row_type: str,
    row_ids: list[int],
    owner_id: int,
) -> None:
    """Delete sealed payloads when their source Runtime rows are deleted."""
    row_id_hashes = [_digest(str(row_id)) for row_id in row_ids]
    if not row_id_hashes:
        return
    runtime_db.execute(
        delete(CoreFSSealedPayload).where(
            CoreFSSealedPayload.row_type == row_type,
            CoreFSSealedPayload.row_id_hash.in_(row_id_hashes),
            CoreFSSealedPayload.owner_id_hash == _digest(str(owner_id)),
        )
    )


def delete_all_sealed_runtime_records_for_owner(
    runtime_db: Session,
    *,
    owner_id: int,
) -> None:
    """Delete every sealed Runtime payload owned by one reset/forgotten user."""
    runtime_db.execute(
        delete(CoreFSSealedPayload).where(
            CoreFSSealedPayload.owner_id_hash == _digest(str(owner_id))
        )
    )


def delete_runtime_embedding_records(
    runtime_db: Session,
    *,
    owner_id: int | None = None,
    source_type: str | None = None,
    source_ids: Sequence[int] | None = None,
) -> int:
    """Delete Runtime embeddings and their polymorphic sealed previews together."""
    from anima_server.models.runtime_embedding import RuntimeEmbedding

    if source_ids is not None and not source_ids:
        return 0
    conditions = []
    if owner_id is not None:
        conditions.append(RuntimeEmbedding.user_id == owner_id)
    if source_type is not None:
        conditions.append(RuntimeEmbedding.source_type == source_type)
    if source_ids is not None:
        conditions.append(RuntimeEmbedding.source_id.in_(list(source_ids)))

    rows = list(
        runtime_db.execute(
            select(
                RuntimeEmbedding.id,
                RuntimeEmbedding.user_id,
                RuntimeEmbedding.source_type,
                RuntimeEmbedding.source_id,
            ).where(*conditions)
        ).all()
    )
    if not rows:
        return 0

    row_ids_by_owner: defaultdict[int, list[int]] = defaultdict(list)
    deleted_sources_by_owner: defaultdict[int, defaultdict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row_id, row_owner_id, row_source_type, row_source_id in rows:
        row_ids_by_owner[int(row_owner_id)].append(int(row_id))
        deleted_sources_by_owner[int(row_owner_id)][str(row_source_type)].add(int(row_source_id))
    for row_owner_id, row_ids in row_ids_by_owner.items():
        delete_sealed_runtime_records(
            runtime_db,
            row_type="runtime_embedding",
            row_ids=row_ids,
            owner_id=row_owner_id,
        )
    runtime_db.execute(
        delete(RuntimeEmbedding).where(
            RuntimeEmbedding.id.in_([int(row_id) for row_id, *_rest in rows])
        )
    )

    def publish() -> None:
        for row_owner_id, sources in deleted_sources_by_owner.items():
            for live_index in active_runtime_indexes(row_owner_id):
                for deleted_source_type, deleted_source_ids in sources.items():
                    with suppress(CoreFSRuntimeLocked):
                        live_index.delete_runtime_embeddings(
                            source_type=deleted_source_type,
                            source_ids=frozenset(deleted_source_ids),
                        )

    _defer_runtime_index_write_until_root_commit(runtime_db, publish)
    return len(rows)


def load_runtime_record(
    runtime_db: Session,
    *,
    row_type: str,
    row_id: int,
    owner_id: int,
) -> dict[str, Any] | None:
    sealed_id = runtime_db.scalar(
        select(CoreFSSealedPayload.id).where(
            CoreFSSealedPayload.row_type == row_type,
            CoreFSSealedPayload.row_id_hash == _digest(str(row_id)),
            CoreFSSealedPayload.owner_id_hash == _digest(str(owner_id)),
        )
    )
    if sealed_id is None:
        return None
    index = _active_runtime_index(owner_id)
    if index is None:
        raise RuntimeSealingLocked("sealed Runtime payload is unavailable while CoreFS is locked")
    return _load_runtime_record_with_index(
        runtime_db,
        index=index,
        row_type=row_type,
        row_id=row_id,
        owner_id=owner_id,
    )


def _load_runtime_record_with_index(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    row_type: str,
    row_id: int,
    owner_id: int,
) -> dict[str, Any] | None:
    aad = RuntimePayloadAAD(
        row_type=row_type,
        row_id=str(row_id),
        owner_id=str(owner_id),
    )
    sealed = runtime_db.scalar(
        select(CoreFSSealedPayload).where(
            CoreFSSealedPayload.row_type == row_type,
            CoreFSSealedPayload.row_id_hash == _digest(str(row_id)),
            CoreFSSealedPayload.owner_id_hash == _digest(str(owner_id)),
        )
    )
    if sealed is None:
        return None
    try:
        if (
            sealed.core_id != index.core_id
            or sealed.local_instance_id != index.local_instance_id
            or sealed.aad_digest != hashlib.sha256(aad.encode()).hexdigest()
        ):
            raise ValueError("sealed Runtime payload binding is invalid")
        raw = index.open_runtime_payload(
            SealedRuntimePayload(
                nonce=bytes(sealed.nonce),
                ciphertext=bytes(sealed.ciphertext),
                version=sealed.key_version,
            ),
            aad=aad,
        )
    except CoreFSRuntimeLocked as exc:
        raise RuntimeSealingLocked(
            "sealed Runtime payload is unavailable while CoreFS is locked"
        ) from exc
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("sealed Runtime payload is invalid")
    return value


def _hydrate_private_runtime_fields(target: Any, _context: Any) -> None:
    specification = _PRIVATE_RUNTIME_FIELD_SPECS.get(type(target))
    if specification is None:
        return
    runtime_db = object_session(target)
    if runtime_db is None:
        return
    row_type, fields = specification
    owner_id = getattr(target, "user_id", None)
    if owner_id is None:
        from anima_server.models.runtime import (
            RuntimeWorkflowCheckpoint,
            RuntimeWorkflowRun,
        )

        if isinstance(target, RuntimeWorkflowCheckpoint):
            owner_id = runtime_db.scalar(
                select(RuntimeWorkflowRun.user_id).where(
                    RuntimeWorkflowRun.id == target.workflow_run_id
                )
            )
    if not isinstance(owner_id, int):
        return
    payload = load_runtime_record(
        runtime_db,
        row_type=row_type,
        row_id=int(target.id),
        owner_id=owner_id,
    )
    if payload is None:
        return
    for field in fields:
        if field in payload:
            set_committed_value(target, field, payload[field])


def _install_private_runtime_hydration() -> dict[type[Any], tuple[str, tuple[str, ...]]]:
    from anima_server.models.runtime import (
        RuntimeBackgroundTaskRun,
        RuntimeDocument,
        RuntimeDocumentChunk,
        RuntimeImageAnnotation,
        RuntimeImageAsset,
        RuntimeKnowledgeBundleRun,
        RuntimeKnowledgeConcept,
        RuntimeKnowledgeConceptSource,
        RuntimeRun,
        RuntimeSource,
        RuntimeSourceArtifact,
        RuntimeSourceSpan,
        RuntimeThread,
        RuntimeWorkflowCheckpoint,
        RuntimeWorkflowRun,
    )
    from anima_server.models.runtime_embedding import RuntimeEmbedding

    specifications = {
        RuntimeDocument: (
            "runtime_document",
            ("filename", "mime_type", "storage_path", "metadata_json"),
        ),
        RuntimeDocumentChunk: (
            "runtime_document_chunk",
            ("content_text", "section_title", "metadata_json"),
        ),
        RuntimeImageAsset: (
            "runtime_image_asset",
            ("filename", "mime_type", "storage_path", "metadata_json"),
        ),
        RuntimeImageAnnotation: ("runtime_image_annotation", ("content_text",)),
        RuntimeSource: (
            "runtime_source",
            ("source_uri", "title", "media_type", "metadata_json"),
        ),
        RuntimeSourceArtifact: (
            "runtime_source_artifact",
            ("content_text", "metadata_json"),
        ),
        RuntimeSourceSpan: (
            "runtime_source_span",
            ("content_text", "metadata_json"),
        ),
        RuntimeEmbedding: ("runtime_embedding", ("content_preview",)),
        RuntimeRun: ("runtime_run", ("error_text",)),
        RuntimeBackgroundTaskRun: (
            "runtime_background_task_run",
            ("result_json", "error_message"),
        ),
        RuntimeWorkflowRun: (
            "runtime_workflow_run",
            ("input_json", "result_json", "error_json"),
        ),
        RuntimeKnowledgeBundleRun: (
            "runtime_knowledge_bundle_run",
            ("input_json", "result_json", "error_json"),
        ),
        RuntimeWorkflowCheckpoint: (
            "runtime_workflow_checkpoint",
            ("input_json", "output_json", "error_json"),
        ),
        RuntimeThread: ("runtime_thread", ("title",)),
        RuntimeKnowledgeConcept: (
            "runtime_knowledge_concept",
            (
                "concept_type",
                "slug",
                "title",
                "description",
                "body_markdown",
                "frontmatter_json",
            ),
        ),
        RuntimeKnowledgeConceptSource: (
            "runtime_knowledge_concept_source",
            ("quote_text",),
        ),
    }
    for model in specifications:
        event.listen(
            model,
            "load",
            _hydrate_private_runtime_fields,
            restore_load_context=True,
        )
    return specifications


_PRIVATE_RUNTIME_FIELD_SPECS = _install_private_runtime_hydration()
