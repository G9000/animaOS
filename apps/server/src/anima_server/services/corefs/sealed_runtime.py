from __future__ import annotations

import hashlib
import json
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


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _active_runtime_index(user_id: int) -> CoreFSProgressiveIndex | None:
    from anima_server.services.sessions import unlock_session_store

    return unlock_session_store.get_active_runtime_index(user_id)


def active_runtime_index(user_id: int) -> CoreFSProgressiveIndex | None:
    return _active_runtime_index(user_id)


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
    if binding is not None:
        raise RuntimeSealingLocked(
            "sensitive Runtime writes are unavailable while CoreFS is locked"
        )
    return None


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
        set_committed_value(row, field, value)


def convert_legacy_runtime_rows(
    runtime_db: Session,
    *,
    index: CoreFSProgressiveIndex,
    user_id: int,
) -> int:
    """Seal and scrub legacy plaintext Runtime rows for one unlocked owner.

    Alembic cannot perform this conversion because the sealing key only exists
    inside an unlock session. Each row is sealed and scrubbed in the caller's
    transaction, and an existing sealed payload makes the pass idempotent.
    """
    from anima_server.models.pending_memory_op import PendingMemoryOp
    from anima_server.models.runtime import (
        RuntimeDocumentChunk,
        RuntimeImageAnnotation,
        RuntimeKnowledgeConcept,
        RuntimeKnowledgeConceptSource,
        RuntimeMessage,
        RuntimeSourceArtifact,
        RuntimeSourceSpan,
        RuntimeStep,
        RuntimeThread,
        RuntimeWorkflowCheckpoint,
        RuntimeWorkflowRun,
    )
    from anima_server.models.runtime_embedding import RuntimeEmbedding
    from anima_server.models.runtime_memory import (
        MemoryCandidate,
        MemoryExtractionFailure,
        ProfileUpdateCandidate,
        RuntimeSessionNote,
    )

    specifications = (
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
            {"content": "content", "tags": "tags_json", "salience": "salience_json"},
            {"content": "", "tags_json": None, "salience_json": None},
        ),
        (
            MemoryExtractionFailure.__table__,
            "memory_extraction_failure",
            {
                "user_message_preview": "user_message_preview",
                "assistant_response_preview": "assistant_response_preview",
            },
            {"user_message_preview": None, "assistant_response_preview": None},
        ),
        (
            ProfileUpdateCandidate.__table__,
            "profile_update_candidate",
            {"value": "value", "evidence_text": "evidence_text"},
            {"value": "", "evidence_text": None},
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
            {"content": "content", "old_content": "old_content"},
            {"content": "", "old_content": None},
        ),
        (
            RuntimeEmbedding.__table__,
            "runtime_embedding",
            {"content_preview": "content_preview"},
            {"content_preview": ""},
        ),
        (
            RuntimeWorkflowRun.__table__,
            "runtime_workflow_run",
            {"result_json": "result_json"},
            {"result_json": None},
        ),
        (
            RuntimeKnowledgeConcept.__table__,
            "runtime_knowledge_concept",
            {"body_markdown": "body_markdown"},
            {"body_markdown": ""},
        ),
        (
            RuntimeKnowledgeConceptSource.__table__,
            "runtime_knowledge_concept_source",
            {"quote_text": "quote_text"},
            {"quote_text": None},
        ),
    )

    converted = 0
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
                checkpoint_table.c.output_json,
            )
            .join(
                workflow_table,
                workflow_table.c.id == checkpoint_table.c.workflow_run_id,
            )
            .where(workflow_table.c.user_id == user_id)
        ),
        payload_columns={"output_json": "output_json"},
        placeholders={"output_json": None},
    )
    runtime_db.flush()
    return converted


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
        if already_sealed is not None:
            continue
        payload = {
            payload_name: row[column_name] for payload_name, column_name in payload_columns.items()
        }
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


def load_runtime_record(
    runtime_db: Session,
    *,
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
    index = _active_runtime_index(owner_id)
    if index is None:
        raise RuntimeSealingLocked("sealed Runtime payload is unavailable while CoreFS is locked")
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
        RuntimeDocumentChunk,
        RuntimeImageAnnotation,
        RuntimeKnowledgeConcept,
        RuntimeKnowledgeConceptSource,
        RuntimeSourceArtifact,
        RuntimeSourceSpan,
        RuntimeWorkflowCheckpoint,
        RuntimeWorkflowRun,
    )
    from anima_server.models.runtime_embedding import RuntimeEmbedding

    specifications = {
        RuntimeDocumentChunk: (
            "runtime_document_chunk",
            ("content_text", "section_title", "metadata_json"),
        ),
        RuntimeImageAnnotation: ("runtime_image_annotation", ("content_text",)),
        RuntimeSourceArtifact: (
            "runtime_source_artifact",
            ("content_text", "metadata_json"),
        ),
        RuntimeSourceSpan: (
            "runtime_source_span",
            ("content_text", "metadata_json"),
        ),
        RuntimeEmbedding: ("runtime_embedding", ("content_preview",)),
        RuntimeWorkflowRun: ("runtime_workflow_run", ("result_json",)),
        RuntimeWorkflowCheckpoint: (
            "runtime_workflow_checkpoint",
            ("output_json",),
        ),
        RuntimeKnowledgeConcept: (
            "runtime_knowledge_concept",
            ("body_markdown",),
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
