from __future__ import annotations

import hashlib
import re
import secrets
from string import hexdigits
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session_async, require_unlocked_user_async
from anima_server.config import settings
from anima_server.db import get_runtime_db
from anima_server.models.runtime import (
    RuntimeDocument,
    RuntimeDocumentChunk,
    RuntimeThread,
    RuntimeWorkflowCheckpoint,
    RuntimeWorkflowRun,
)
from anima_server.services.corefs.asset_authority import (
    CoreFsSourceError,
    asset_authority_selection,
    open_corefs_byte_source,
)
from anima_server.services.corefs.asset_mutations import (
    AssetMutationError,
    upsert_canonical_binary_asset,
)
from anima_server.services.corefs.diary_migration import migration_opaque_id
from anima_server.services.documents import (
    DocumentStoragePathError,
    resolve_document_storage_path,
)
from anima_server.services.documents.parsing_pack import ensure_parsing_pack, pack_status
from anima_server.services.documents.pdf_workflow import (
    PDFIngestionDependencies,
    PDFIngestionRequest,
    approve_pdf_memory_proposals,
    default_pdf_ingestion_dependencies,
    resume_pdf_ingestion_workflow,
    start_pdf_ingestion_workflow,
)
from anima_server.services.documents.rag import search_document_chunks
from anima_server.services.documents.reparse import reparse_document
from anima_server.services.documents.store import resolve_document_byte_source

router = APIRouter(prefix="/api/documents", tags=["documents"])

_PDF_WORKFLOW_TYPE = "pdf_ingestion"
_PDF_STORAGE_PREFIX = ".anima/documents"


class StartPDFWorkflowRequest(BaseModel):
    userId: int = Field(ge=0)
    filename: str = Field(min_length=1, max_length=255)
    mimeType: str = Field(min_length=1, max_length=128)
    storagePath: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64)
    sizeBytes: int = Field(gt=0)
    threadId: int | None = Field(default=None, ge=1)
    metadata: dict[str, Any] | None = None

    @field_validator("filename", "storagePath")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Value must not be blank.")
        return stripped

    @field_validator("mimeType")
    @classmethod
    def require_pdf_mime_type(cls, value: str) -> str:
        if value != "application/pdf":
            raise ValueError("Only application/pdf documents are supported.")
        return value

    @field_validator("sha256")
    @classmethod
    def require_sha256ish(cls, value: str) -> str:
        if len(value) != 64 or any(char not in hexdigits for char in value):
            raise ValueError("sha256 must be a 64-character hexadecimal string.")
        return value.lower()


class SearchDocumentsRequest(BaseModel):
    userId: int = Field(ge=0)
    query: str = Field(min_length=1, max_length=2000)
    documentIds: list[int] | None = None
    limit: int = Field(default=8, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def require_query_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Query must not be blank.")
        return stripped

    @model_validator(mode="after")
    def validate_document_ids(self) -> SearchDocumentsRequest:
        if self.documentIds is not None and any(document_id <= 0 for document_id in self.documentIds):
            raise ValueError("documentIds must contain positive ids.")
        return self


class ApproveMemoryRequest(BaseModel):
    proposalIndices: list[int] = Field()

    @field_validator("proposalIndices")
    @classmethod
    def require_nonnegative_indices(cls, value: list[int]) -> list[int]:
        if any(index < 0 for index in value):
            raise ValueError("proposalIndices must be non-negative.")
        return value


@router.post(
    "/pdf",
    status_code=status.HTTP_201_CREATED,
)
async def upload_pdf_document(
    request: Request,
    userId: int = Form(..., ge=0),
    threadId: int | None = Form(default=None, ge=1),
    file: UploadFile = File(...),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    session = await require_unlocked_user_async(request, userId)
    if threadId is not None:
        _load_owned_thread(runtime_db, threadId, user_id=userId)

    filename = _sanitize_pdf_filename(file.filename or "")
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only application/pdf documents are supported.",
        )

    data = await file.read(settings.diary_attachment_max_size_bytes + 1)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PDF file must not be empty.",
        )
    if len(data) > settings.diary_attachment_max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "PDF is too large. "
                f"Limit is {settings.diary_attachment_max_size_bytes} bytes."
            ),
        )
    if not data.lstrip().startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid PDF.",
        )

    sha256 = hashlib.sha256(data).hexdigest()
    if asset_authority_selection(session) is not None:
        existing = runtime_db.scalar(
            select(RuntimeDocument).where(
                RuntimeDocument.user_id == userId,
                RuntimeDocument.sha256 == sha256,
            )
        )
        try:
            if existing is not None:
                source = resolve_document_byte_source(existing, user_id=userId)
                if isinstance(source, str):
                    raise CoreFsSourceError("Canonical document source is unavailable.")
                if (
                    source.content_sha256 != sha256
                    or source.size != len(data)
                    or source.content_type != "application/pdf"
                ):
                    raise CoreFsSourceError("Canonical document source changed identity.")
                storage_path = f"corefs://object/{source.stable_id}"
            else:
                stable_id = migration_opaque_id("document-upload", sha256)
                upsert_canonical_binary_asset(
                    session=session,
                    stable_id=stable_id,
                    name=filename,
                    object_kind="attachment",
                    content_type="application/pdf",
                    data=data,
                )
                storage_path = f"corefs://object/{stable_id}"
        except (AssetMutationError, CoreFsSourceError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
    else:
        storage_path = _document_storage_path(userId, filename)
        try:
            resolved_path = resolve_document_storage_path(storage_path, user_id=userId)
        except DocumentStoragePathError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path.write_bytes(data)

    run = start_pdf_ingestion_workflow(
        runtime_db,
        PDFIngestionRequest(
            user_id=userId,
            filename=filename,
            mime_type="application/pdf",
            storage_path=storage_path,
            sha256=sha256,
            size_bytes=len(data),
            thread_id=threadId,
            metadata_json={"source": "chat_upload"},
        ),
    )
    runtime_db.flush()
    response = _workflow_action_response(run)
    response["document"] = {
        "filename": filename,
        "mimeType": "application/pdf",
        "storagePath": storage_path,
        "sha256": sha256,
        "sizeBytes": len(data),
    }
    return response


@router.post(
    "/workflows/pdf",
    status_code=status.HTTP_201_CREATED,
)
async def start_pdf_workflow(
    payload: StartPDFWorkflowRequest,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    session = await require_unlocked_user_async(request, payload.userId)
    if asset_authority_selection(session) is not None:
        try:
            source = open_corefs_byte_source(
                session=session,
                object_uri=payload.storagePath,
                expected_kinds=frozenset({"attachment"}),
            )
        except CoreFsSourceError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Canonical document source is unavailable.",
            ) from exc
        if (
            source.content_type != payload.mimeType
            or source.content_sha256 != payload.sha256
            or source.size != payload.sizeBytes
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Canonical document source changed identity.",
            )
    else:
        try:
            resolve_document_storage_path(payload.storagePath, user_id=payload.userId)
        except DocumentStoragePathError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
    if payload.threadId is not None:
        _load_owned_thread(runtime_db, payload.threadId, user_id=payload.userId)

    run = start_pdf_ingestion_workflow(
        runtime_db,
        PDFIngestionRequest(
            user_id=payload.userId,
            filename=payload.filename,
            mime_type=payload.mimeType,
            storage_path=payload.storagePath,
            sha256=payload.sha256,
            size_bytes=payload.sizeBytes,
            thread_id=payload.threadId,
            metadata_json=payload.metadata,
        ),
    )
    runtime_db.flush()
    return _workflow_action_response(run)


@router.get("/workflows/{workflow_id}")
async def get_workflow_status(
    workflow_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    session = await require_unlocked_session_async(request)
    run = _load_owned_workflow(runtime_db, workflow_id, user_id=session.user_id)
    return _workflow_to_response(run, runtime_db)


@router.post("/workflows/{workflow_id}/resume")
async def resume_workflow(
    workflow_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    session = await require_unlocked_session_async(request)
    _load_owned_workflow(runtime_db, workflow_id, user_id=session.user_id)

    try:
        run = resume_pdf_ingestion_workflow(
            runtime_db,
            workflow_run_id=workflow_id,
            dependencies=_default_pdf_dependencies(),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"PDF ingestion is unavailable: {exc}",
        ) from exc
    except ValueError as exc:
        status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if "was not fully indexed" in str(exc)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    runtime_db.flush()
    return _workflow_action_response(run, runtime_db)


@router.post("/workflows/{workflow_id}/approve-memory")
async def approve_memory(
    workflow_id: int,
    payload: ApproveMemoryRequest,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    session = await require_unlocked_session_async(request)
    _load_owned_workflow(runtime_db, workflow_id, user_id=session.user_id)

    try:
        run = approve_pdf_memory_proposals(
            runtime_db,
            workflow_run_id=workflow_id,
            approved_proposal_indices=payload.proposalIndices,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    runtime_db.flush()
    return _workflow_action_response(run, runtime_db)


@router.post("/search")
async def search_documents(
    payload: SearchDocumentsRequest,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    await require_unlocked_user_async(request, payload.userId)
    results = search_document_chunks(
        runtime_db,
        payload.userId,
        payload.query,
        document_ids=payload.documentIds,
        limit=payload.limit,
    )
    serialized = [
        {
            "chunkId": result.chunk_id,
            "documentId": result.document_id,
            "filename": result.filename,
            "content": result.content,
            "similarity": result.similarity,
            "pageStart": result.page_start,
            "pageEnd": result.page_end,
            "sectionTitle": result.section_title,
        }
        for result in results
    ]
    return {"count": len(serialized), "results": serialized}


@router.get("/parsing-pack")
async def get_parsing_pack_status(request: Request) -> dict[str, Any]:
    await require_unlocked_session_async(request)
    return _parsing_pack_response(pack_status())


@router.post("/parsing-pack/download")
async def download_parsing_pack(request: Request) -> dict[str, Any]:
    await require_unlocked_session_async(request)
    return _parsing_pack_response(ensure_parsing_pack())


@router.post("/{document_id}/reparse")
async def reparse_document_route(
    document_id: int,
    request: Request,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    session = await require_unlocked_session_async(request)
    try:
        result = reparse_document(
            runtime_db,
            user_id=session.user_id,
            document_id=document_id,
        )
    except DocumentStoragePathError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"PDF parsing is unavailable: {exc}",
        ) from exc
    if result.status == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if result.status == "pack_not_ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Parsing pack is not ready.",
        )
    if result.status == "parse_degraded":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Quality parsing failed for this document; try again.",
        )
    if result.status == "parser_unavailable":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"PDF parsing is unavailable: {result.detail}",
        )
    runtime_db.flush()
    return {"status": result.status, "chunk_count": result.chunk_count}


def _parsing_pack_response(pack: Any) -> dict[str, Any]:
    return {"state": pack.state, "progress": pack.progress, "error": pack.error}


def _load_owned_workflow(
    runtime_db: Session,
    workflow_id: int,
    *,
    user_id: int,
) -> RuntimeWorkflowRun:
    run = runtime_db.get(RuntimeWorkflowRun, workflow_id)
    if run is None or run.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    if run.workflow_type != _PDF_WORKFLOW_TYPE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return run


def _load_owned_thread(
    runtime_db: Session,
    thread_id: int,
    *,
    user_id: int,
) -> RuntimeThread:
    thread = runtime_db.get(RuntimeThread, thread_id)
    if thread is None or thread.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found")
    return thread


def _workflow_action_response(
    run: RuntimeWorkflowRun,
    runtime_db: Session | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "workflowId": run.id,
        "status": run.status,
        "currentState": run.current_state,
    }
    if runtime_db is not None:
        response["workflow"] = _workflow_to_response(run, runtime_db)
    return response


def _workflow_to_response(
    run: RuntimeWorkflowRun,
    runtime_db: Session,
) -> dict[str, Any]:
    checkpoints = list(
        runtime_db.scalars(
            select(RuntimeWorkflowCheckpoint)
            .where(RuntimeWorkflowCheckpoint.workflow_run_id == run.id)
            .order_by(RuntimeWorkflowCheckpoint.checkpoint_index)
        ).all()
    )
    return {
        "id": run.id,
        "userId": run.user_id,
        "threadId": run.thread_id,
        "workflowType": run.workflow_type,
        "status": run.status,
        "currentState": run.current_state,
        "input": run.input_json,
        "result": run.result_json,
        "error": run.error_json,
        "retryCount": run.retry_count,
        "maxRetries": run.max_retries,
        "createdAt": run.created_at,
        "updatedAt": run.updated_at,
        "startedAt": run.started_at,
        "completedAt": run.completed_at,
        "checkpoints": [_checkpoint_to_response(checkpoint) for checkpoint in checkpoints],
    }


def _checkpoint_to_response(checkpoint: RuntimeWorkflowCheckpoint) -> dict[str, Any]:
    return {
        "id": checkpoint.id,
        "index": checkpoint.checkpoint_index,
        "state": checkpoint.state_name,
        "status": checkpoint.status,
        "input": checkpoint.input_json,
        "output": checkpoint.output_json,
        "artifacts": checkpoint.artifact_refs_json,
        "error": checkpoint.error_json,
        "createdAt": checkpoint.created_at,
    }


def _sanitize_pdf_filename(filename: str) -> str:
    name = filename.strip().replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    if not name:
        name = "document.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    if len(name) > 240:
        stem = name[:-4].rstrip(" .")[:236]
        name = f"{stem}.pdf"
    return name


def _document_storage_path(user_id: int, filename: str) -> str:
    return f"{_PDF_STORAGE_PREFIX}/{user_id}/{secrets.token_hex(8)}_{filename}"


def _default_pdf_dependencies() -> PDFIngestionDependencies:
    return default_pdf_ingestion_dependencies(
        summarize=_summarize_document,
        propose_facts=_propose_no_facts,
    )


def _summarize_document(
    document: RuntimeDocument,
    chunks: list[RuntimeDocumentChunk],
) -> dict[str, Any]:
    return {
        "title": document.filename,
        "chunk_count": len(chunks),
        "summary": f"Indexed {len(chunks)} chunks from {document.filename}.",
    }


def _propose_no_facts(
    _document: RuntimeDocument,
    _chunks: list[RuntimeDocumentChunk],
    _summary: dict[str, Any],
) -> list[dict[str, Any]]:
    return []
