from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import (
    RuntimeDocument,
    RuntimeDocumentChunk,
    RuntimeWorkflowCheckpoint,
    RuntimeWorkflowRun,
)
from anima_server.services.agent.candidate_ops import create_memory_candidate
from anima_server.services.agent.embeddings import generate_embedding
from anima_server.services.documents.chunking import chunk_pages
from anima_server.services.documents.indexing import embed_document_chunks
from anima_server.services.documents.models import (
    DocumentRegistration,
    ExtractedDocumentChunk,
)
from anima_server.services.documents.pdf_text import PageText, extract_pdf_text
from anima_server.services.documents.store import (
    get_document_for_user,
    list_document_chunks,
    register_document,
    replace_document_chunks,
)
from anima_server.services.workflows import (
    append_checkpoint,
    load_resume_point,
    mark_workflow_awaiting_input,
    mark_workflow_completed,
    start_workflow,
)

PDF_WORKFLOW_STATES = (
    "created",
    "file_registered",
    "text_extracted",
    "chunked",
    "embedded",
    "indexed",
    "summarized",
    "facts_proposed",
    "awaiting_approval",
    "memory_saved",
    "memory_rejected",
    "completed",
)

PDF_WORKFLOW_TYPE = "pdf_ingestion"
TERMINAL_WORKFLOW_STATUSES = frozenset({"completed", "failed", "cancelled"})

JsonObject = dict[str, Any]
ExtractTextFn = Callable[[str], Sequence[PageText]]
ChunkTextFn = Callable[[Sequence[PageText]], Sequence[ExtractedDocumentChunk]]
EmbeddingFn = Callable[[str], list[float] | None]
SummarizeFn = Callable[[RuntimeDocument, list[RuntimeDocumentChunk]], JsonObject]
ProposeFactsFn = Callable[
    [RuntimeDocument, list[RuntimeDocumentChunk], JsonObject],
    list[JsonObject],
]


@dataclass(frozen=True, slots=True)
class PDFIngestionRequest:
    user_id: int
    filename: str
    mime_type: str
    storage_path: str
    sha256: str
    size_bytes: int
    thread_id: int | None = None
    metadata_json: JsonObject | None = None

    def to_input_json(self) -> JsonObject:
        return {
            "user_id": self.user_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "storage_path": self.storage_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "thread_id": self.thread_id,
            "metadata_json": self.metadata_json,
        }

    def to_registration(self, *, workflow_run_id: int | None = None) -> DocumentRegistration:
        return DocumentRegistration(
            user_id=self.user_id,
            filename=self.filename,
            mime_type=self.mime_type,
            storage_path=self.storage_path,
            sha256=self.sha256,
            size_bytes=self.size_bytes,
            thread_id=self.thread_id,
            workflow_run_id=workflow_run_id,
            metadata_json=self.metadata_json,
        )


@dataclass(frozen=True, slots=True)
class PDFIngestionDependencies:
    extract_text: ExtractTextFn
    chunk_text: ChunkTextFn
    embedding_fn: EmbeddingFn
    summarize: SummarizeFn
    propose_facts: ProposeFactsFn


def default_pdf_ingestion_dependencies(
    *,
    summarize: SummarizeFn,
    propose_facts: ProposeFactsFn,
    embedding_fn: EmbeddingFn | None = None,
) -> PDFIngestionDependencies:
    return PDFIngestionDependencies(
        extract_text=extract_pdf_text,
        chunk_text=chunk_pages,
        embedding_fn=embedding_fn or generate_embedding,
        summarize=summarize,
        propose_facts=propose_facts,
    )


def start_pdf_ingestion_workflow(
    db: Session,
    request: PDFIngestionRequest,
    *,
    max_retries: int = 3,
) -> RuntimeWorkflowRun:
    return start_workflow(
        db,
        user_id=request.user_id,
        thread_id=request.thread_id,
        workflow_type=PDF_WORKFLOW_TYPE,
        input_json=request.to_input_json(),
        max_retries=max_retries,
    )


def resume_pdf_ingestion_workflow(
    db: Session,
    *,
    workflow_run_id: int,
    dependencies: PDFIngestionDependencies,
) -> RuntimeWorkflowRun:
    return run_pdf_ingestion_until_wait_or_done(
        db,
        workflow_run_id=workflow_run_id,
        dependencies=dependencies,
    )


def approve_pdf_memory_proposals(
    db: Session,
    *,
    workflow_run_id: int,
    approved_proposal_indices: Sequence[int] | None = None,
    approved_proposals: Sequence[JsonObject] | None = None,
) -> RuntimeWorkflowRun:
    run = _load_pdf_approval_run(db, workflow_run_id=workflow_run_id)
    if _is_completed_pdf_memory_decision(run):
        return run
    _require_awaiting_pdf_memory_approval(run)

    staged = _staged_approval_payload(run)
    document_id = _document_id_from_payload(staged)
    selected_proposals = _select_approved_pdf_proposals(
        staged,
        approved_proposal_indices=approved_proposal_indices,
        approved_proposals=approved_proposals,
    )
    candidate_ids: list[int] = []
    for normalized in selected_proposals:
        candidate = create_memory_candidate(
            db,
            user_id=run.user_id,
            content=normalized["content"],
            category=normalized["category"],
            importance=normalized["importance"],
            importance_source="user_explicit",
            source="tool",
            extraction_model="pdf_workflow",
            tags=_pdf_memory_candidate_tags(
                workflow_run_id=run.id,
                document_id=document_id,
            ),
        )
        if candidate is not None:
            candidate_ids.append(candidate.id)

    result_json: JsonObject = {
        "document_id": document_id,
        "decision": "approved",
        "selected_count": len(selected_proposals),
        "created_count": len(candidate_ids),
        "candidate_ids": candidate_ids,
    }
    append_checkpoint(
        db,
        workflow_run_id=run.id,
        state_name="memory_saved",
        status="completed",
        idempotency_key=_idempotency_key(run.id, "memory_saved"),
        input_json={
            "selected_count": len(selected_proposals),
            "created_count": len(candidate_ids),
        },
        output_json=result_json,
        artifact_refs_json={
            "document_id": document_id,
            "candidate_ids": candidate_ids,
        },
    )
    mark_workflow_completed(db, run, result_json=result_json)
    return run


def reject_pdf_memory_proposals(
    db: Session,
    *,
    workflow_run_id: int,
    reason: str | None = None,
) -> RuntimeWorkflowRun:
    run = _load_pdf_approval_run(db, workflow_run_id=workflow_run_id)
    if _is_completed_pdf_memory_decision(run):
        return run
    _require_awaiting_pdf_memory_approval(run)

    staged = _staged_approval_payload(run)
    document_id = _document_id_from_payload(staged)
    result_json: JsonObject = {
        "document_id": document_id,
        "decision": "rejected",
        "reason": reason,
    }
    append_checkpoint(
        db,
        workflow_run_id=run.id,
        state_name="memory_rejected",
        status="completed",
        idempotency_key=_idempotency_key(run.id, "memory_rejected"),
        input_json={"reason": reason},
        output_json=result_json,
        artifact_refs_json={"document_id": document_id},
    )
    mark_workflow_completed(db, run, result_json=result_json)
    return run


def run_pdf_ingestion_until_wait_or_done(
    db: Session,
    *,
    workflow_run_id: int,
    dependencies: PDFIngestionDependencies,
) -> RuntimeWorkflowRun:
    resume_point = load_resume_point(db, workflow_run_id=workflow_run_id)
    if resume_point is None:
        raise ValueError(f"Workflow run {workflow_run_id} does not exist.")

    run = resume_point.run
    if run.workflow_type != PDF_WORKFLOW_TYPE:
        raise ValueError(
            f"Workflow run {workflow_run_id} is {run.workflow_type!r}, "
            f"not {PDF_WORKFLOW_TYPE!r}."
        )
    if run.status in TERMINAL_WORKFLOW_STATUSES:
        return run
    if run.input_json is None:
        raise ValueError(f"Workflow run {workflow_run_id} has no PDF input.")

    request = _request_from_input(run.input_json)
    context = _WorkflowContext(db, run, request)
    latest_completed = (
        resume_point.latest_checkpoint.state_name
        if resume_point.latest_checkpoint is not None
        else "created"
    )

    while True:
        next_state = _next_state_after(latest_completed)
        if next_state is None or next_state in {"memory_saved", "completed"}:
            return run

        if next_state == "file_registered":
            document = register_document(
                db,
                request.to_registration(workflow_run_id=run.id),
            )
            context.document = document
            _append_completed(
                db,
                run,
                "file_registered",
                output_json={"document_id": document.id},
                artifact_refs_json={"document_id": document.id},
            )

        elif next_state == "text_extracted":
            document = context.require_document()
            pages = list(dependencies.extract_text(document.storage_path))
            _append_completed(
                db,
                run,
                "text_extracted",
                output_json={"document_id": document.id, "pages": _json_safe(pages)},
                artifact_refs_json={"document_id": document.id},
            )

        elif next_state == "chunked":
            document = context.require_document()
            pages = context.require_pages()
            extracted_chunks = list(dependencies.chunk_text(pages))
            chunks = replace_document_chunks(
                db,
                document_id=document.id,
                chunks=extracted_chunks,
            )
            context.chunks = chunks
            _append_completed(
                db,
                run,
                "chunked",
                output_json={"document_id": document.id, "chunk_count": len(chunks)},
                artifact_refs_json={
                    "document_id": document.id,
                    "chunk_ids": [chunk.id for chunk in chunks],
                },
            )

        elif next_state == "embedded":
            document = context.require_document()
            embedded_count = embed_document_chunks(
                db,
                user_id=run.user_id,
                document_id=document.id,
                embedding_fn=dependencies.embedding_fn,
            )
            document = context.require_document(refresh=True)
            if document.status != "indexed":
                raise ValueError(
                    f"PDF document {document.id} was not fully indexed; "
                    "resume after missing embeddings are available."
                )
            _append_completed(
                db,
                run,
                "embedded",
                output_json={
                    "document_id": document.id,
                    "embedded_count": embedded_count,
                },
                artifact_refs_json={"document_id": document.id},
            )

        elif next_state == "indexed":
            document = context.require_document(refresh=True)
            if document.status != "indexed":
                raise ValueError(
                    f"PDF document {document.id} was not fully indexed; "
                    "resume after missing embeddings are available."
                )
            _append_completed(
                db,
                run,
                "indexed",
                output_json={
                    "document_id": document.id,
                    "status": document.status,
                    "indexed": document.status == "indexed",
                },
                artifact_refs_json={"document_id": document.id},
            )

        elif next_state == "summarized":
            document = context.require_document()
            chunks = context.require_chunks()
            summary = dependencies.summarize(document, chunks)
            _append_completed(
                db,
                run,
                "summarized",
                output_json={
                    "document_id": document.id,
                    "summary": _json_safe(summary),
                },
                artifact_refs_json={"document_id": document.id},
            )

        elif next_state == "facts_proposed":
            document = context.require_document()
            chunks = context.require_chunks()
            summary = context.require_summary()
            proposed_facts = dependencies.propose_facts(document, chunks, summary)
            _append_completed(
                db,
                run,
                "facts_proposed",
                output_json={
                    "document_id": document.id,
                    "proposed_facts": _json_safe(proposed_facts),
                },
                artifact_refs_json={"document_id": document.id},
            )

        elif next_state == "awaiting_approval":
            document = context.require_document()
            result_json = {
                "document_id": document.id,
                "summary": context.require_summary(),
                "proposed_facts": context.require_proposed_facts(),
            }
            append_checkpoint(
                db,
                workflow_run_id=run.id,
                state_name="awaiting_approval",
                status="awaiting_input",
                idempotency_key=_idempotency_key(run.id, "awaiting_approval"),
                output_json=result_json,
                artifact_refs_json={"document_id": document.id},
            )
            mark_workflow_awaiting_input(
                db,
                run,
                state_name="awaiting_approval",
                result_json=result_json,
            )
            return run

        latest_completed = next_state


class _WorkflowContext:
    def __init__(
        self,
        db: Session,
        run: RuntimeWorkflowRun,
        request: PDFIngestionRequest,
    ) -> None:
        self.db = db
        self.run = run
        self.request = request
        self.document: RuntimeDocument | None = None
        self.chunks: list[RuntimeDocumentChunk] | None = None

    def require_document(self, *, refresh: bool = False) -> RuntimeDocument:
        if self.document is not None and not refresh:
            return self.document

        document_id = self._document_id_from_checkpoints()
        document = None
        if document_id is not None:
            document = get_document_for_user(
                self.db,
                user_id=self.run.user_id,
                document_id=document_id,
            )
        if document is None:
            document = register_document(
                self.db,
                self.request.to_registration(workflow_run_id=self.run.id),
            )
        self.document = document
        return document

    def require_pages(self) -> list[PageText]:
        checkpoint = self._checkpoint("text_extracted")
        if checkpoint is None or checkpoint.output_json is None:
            raise ValueError("Cannot chunk PDF before text_extracted checkpoint.")
        return _pages_from_checkpoint_payload(checkpoint.output_json.get("pages", []))

    def require_chunks(self) -> list[RuntimeDocumentChunk]:
        if self.chunks is not None:
            return self.chunks
        document = self.require_document()
        self.chunks = list_document_chunks(self.db, document_id=document.id)
        return self.chunks

    def require_summary(self) -> JsonObject:
        checkpoint = self._checkpoint("summarized")
        if checkpoint is None or checkpoint.output_json is None:
            raise ValueError("Cannot propose facts before summarized checkpoint.")
        summary = checkpoint.output_json.get("summary")
        if not isinstance(summary, dict):
            raise ValueError("Summarized checkpoint does not contain a summary object.")
        return summary

    def require_proposed_facts(self) -> list[JsonObject]:
        checkpoint = self._checkpoint("facts_proposed")
        if checkpoint is None or checkpoint.output_json is None:
            raise ValueError(
                "Cannot await approval before facts_proposed checkpoint."
            )
        facts = checkpoint.output_json.get("proposed_facts")
        if not isinstance(facts, list):
            raise ValueError("Facts checkpoint does not contain proposed facts.")
        return [fact for fact in facts if isinstance(fact, dict)]

    def _document_id_from_checkpoints(self) -> int | None:
        for state_name in reversed(PDF_WORKFLOW_STATES):
            checkpoint = self._checkpoint(state_name)
            if checkpoint is None:
                continue
            for payload in (
                checkpoint.artifact_refs_json,
                checkpoint.output_json,
            ):
                if not isinstance(payload, dict):
                    continue
                document_id = payload.get("document_id")
                if isinstance(document_id, int):
                    return document_id
        return None

    def _checkpoint(self, state_name: str) -> RuntimeWorkflowCheckpoint | None:
        return self.db.scalar(
            select(RuntimeWorkflowCheckpoint)
            .where(
                RuntimeWorkflowCheckpoint.workflow_run_id == self.run.id,
                RuntimeWorkflowCheckpoint.state_name == state_name,
            )
            .order_by(RuntimeWorkflowCheckpoint.checkpoint_index.desc())
            .limit(1)
        )


def _append_completed(
    db: Session,
    run: RuntimeWorkflowRun,
    state_name: str,
    *,
    output_json: JsonObject | None = None,
    artifact_refs_json: JsonObject | None = None,
) -> None:
    append_checkpoint(
        db,
        workflow_run_id=run.id,
        state_name=state_name,
        status="completed",
        idempotency_key=_idempotency_key(run.id, state_name),
        output_json=output_json,
        artifact_refs_json=artifact_refs_json,
    )


def _idempotency_key(workflow_run_id: int, state_name: str) -> str:
    return f"pdf:{workflow_run_id}:{state_name}"


def _load_pdf_approval_run(
    db: Session,
    *,
    workflow_run_id: int,
) -> RuntimeWorkflowRun:
    run = db.get(RuntimeWorkflowRun, workflow_run_id)
    if run is None:
        raise ValueError(f"Workflow run {workflow_run_id} does not exist.")
    if run.workflow_type != PDF_WORKFLOW_TYPE:
        raise ValueError(
            f"Workflow run {workflow_run_id} is {run.workflow_type!r}, "
            f"not {PDF_WORKFLOW_TYPE!r}."
        )
    return run


def _is_completed_pdf_memory_decision(run: RuntimeWorkflowRun) -> bool:
    if run.status != "completed" or not isinstance(run.result_json, dict):
        return False
    return run.result_json.get("decision") in {"approved", "rejected"}


def _require_awaiting_pdf_memory_approval(run: RuntimeWorkflowRun) -> None:
    if run.status != "awaiting_input" or run.current_state != "awaiting_approval":
        raise ValueError(
            f"Workflow run {run.id} is not awaiting PDF memory approval."
        )


def _staged_approval_payload(run: RuntimeWorkflowRun) -> JsonObject:
    if not isinstance(run.result_json, dict):
        raise ValueError(f"Workflow run {run.id} has no staged PDF proposals.")
    proposed_facts = run.result_json.get("proposed_facts")
    if not isinstance(proposed_facts, list):
        raise ValueError(f"Workflow run {run.id} has no staged PDF proposals.")
    return run.result_json


def _document_id_from_payload(payload: JsonObject) -> int | None:
    document_id = payload.get("document_id")
    if isinstance(document_id, int):
        return document_id
    return None


def _select_approved_pdf_proposals(
    staged_payload: JsonObject,
    *,
    approved_proposal_indices: Sequence[int] | None,
    approved_proposals: Sequence[JsonObject] | None,
) -> list[JsonObject]:
    if approved_proposal_indices is not None and approved_proposals is not None:
        raise ValueError("Use either approved_proposal_indices or approved_proposals.")

    if approved_proposal_indices is None and approved_proposals is None:
        return _normalized_staged_pdf_proposals(staged_payload)
    if approved_proposal_indices is not None:
        return _select_staged_pdf_proposals_by_index(
            _raw_staged_pdf_proposals(staged_payload),
            approved_proposal_indices,
        )
    if approved_proposals is not None:
        return _select_staged_pdf_proposals_by_payload(
            _normalized_staged_pdf_proposals(staged_payload),
            approved_proposals,
        )
    return []


def _raw_staged_pdf_proposals(staged_payload: JsonObject) -> list[object]:
    raw_proposals = staged_payload.get("proposed_facts", [])
    if not isinstance(raw_proposals, list):
        return []
    return raw_proposals


def _normalized_staged_pdf_proposals(staged_payload: JsonObject) -> list[JsonObject]:
    normalized: list[JsonObject] = []
    for proposal in _raw_staged_pdf_proposals(staged_payload):
        if not isinstance(proposal, dict):
            continue
        normalized_proposal = _normalize_pdf_proposal(proposal)
        if normalized_proposal is not None:
            normalized.append(normalized_proposal)
    return normalized


def _select_staged_pdf_proposals_by_index(
    raw_proposals: Sequence[object],
    approved_proposal_indices: Sequence[int],
) -> list[JsonObject]:
    selected: list[JsonObject] = []
    seen_indices: set[int] = set()
    for index in approved_proposal_indices:
        if type(index) is not int:
            raise ValueError("PDF memory proposal index must be an integer.")
        if index < 0 or index >= len(raw_proposals):
            raise ValueError(f"PDF memory proposal index {index} is not staged.")
        if index in seen_indices:
            continue
        raw_proposal = raw_proposals[index]
        if not isinstance(raw_proposal, dict):
            raise ValueError(f"PDF memory proposal index {index} is not staged.")
        normalized_proposal = _normalize_pdf_proposal(raw_proposal)
        if normalized_proposal is None:
            raise ValueError(f"PDF memory proposal index {index} is not staged.")
        selected.append(normalized_proposal)
        seen_indices.add(index)
    return selected


def _select_staged_pdf_proposals_by_payload(
    staged_proposals: Sequence[JsonObject],
    approved_proposals: Sequence[JsonObject],
) -> list[JsonObject]:
    selected: list[JsonObject] = []
    available = list(staged_proposals)
    for proposal in approved_proposals:
        normalized = _normalize_pdf_proposal(proposal)
        if normalized is None:
            raise ValueError("Approved PDF memory proposal is not staged.")
        try:
            index = available.index(normalized)
        except ValueError as exc:
            raise ValueError(
                "Approved PDF memory proposal is not staged."
            ) from exc
        selected.append(available.pop(index))
    return selected


def _normalize_pdf_proposal(proposal: JsonObject) -> JsonObject | None:
    content = proposal.get("content")
    if not isinstance(content, str) or not content.strip():
        return None

    category = proposal.get("category")
    importance = proposal.get("importance")
    return {
        "content": content.strip(),
        "category": category if isinstance(category, str) else "fact",
        "importance": importance if isinstance(importance, int) else 3,
    }


def _pdf_memory_candidate_tags(
    *,
    workflow_run_id: int,
    document_id: int | None,
) -> list[str]:
    tags = ["pdf"]
    if document_id is not None:
        tags.append(f"document:{document_id}")
    tags.append(f"workflow:{workflow_run_id}")
    return tags


def _next_state_after(state_name: str) -> str | None:
    try:
        index = PDF_WORKFLOW_STATES.index(state_name)
    except ValueError as exc:
        raise ValueError(
            f"Unknown PDF workflow checkpoint state: {state_name!r}."
        ) from exc
    next_index = index + 1
    if next_index >= len(PDF_WORKFLOW_STATES):
        return None
    return PDF_WORKFLOW_STATES[next_index]


def _request_from_input(input_json: JsonObject) -> PDFIngestionRequest:
    return PDFIngestionRequest(
        user_id=int(input_json["user_id"]),
        filename=str(input_json["filename"]),
        mime_type=str(input_json["mime_type"]),
        storage_path=str(input_json["storage_path"]),
        sha256=str(input_json["sha256"]),
        size_bytes=int(input_json["size_bytes"]),
        thread_id=(
            int(input_json["thread_id"])
            if input_json.get("thread_id") is not None
            else None
        ),
        metadata_json=(
            dict(input_json["metadata_json"])
            if isinstance(input_json.get("metadata_json"), dict)
            else None
        ),
    )


def _pages_from_checkpoint_payload(payload: object) -> list[PageText]:
    if not isinstance(payload, list):
        return []

    pages: list[PageText] = []
    for item in payload:
        if isinstance(item, PageText):
            pages.append(item)
            continue
        if not isinstance(item, dict):
            continue

        page_number = item.get("page_number", item.get("page"))
        text = item.get("text")
        if page_number is None or text is None:
            continue
        pages.append(PageText(page_number=int(page_number), text=str(text)))
    return pages


def _json_safe(value: object) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        return _json_safe(asdict(value))
    return value
