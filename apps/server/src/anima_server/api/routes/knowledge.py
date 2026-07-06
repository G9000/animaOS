from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_runtime_db
from anima_server.models.runtime import (
    RuntimeKnowledgeBundleRun,
    RuntimeKnowledgeConcept,
    RuntimeSource,
    RuntimeSourceArtifact,
    RuntimeSourceSpan,
)
from anima_server.services.ingestion.adapters.text import (
    ingest_markdown_content,
    ingest_text_content,
)
from anima_server.services.ingestion.adapters.web import ingest_web_capture
from anima_server.services.ingestion.sources import complete_bundle_run, start_bundle_run

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class TextSourceRequest(BaseModel):
    userId: int = Field(ge=0)
    content: str = Field(min_length=1)
    filename: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=512)
    compile: bool = False

    @field_validator("content")
    @classmethod
    def require_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be empty")
        return value


class WebCaptureRequest(BaseModel):
    userId: int = Field(ge=0)
    url: str = Field(min_length=1, max_length=2048)
    readableText: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=512)
    canonicalUrl: str | None = Field(default=None, max_length=2048)
    compile: bool = False

    @field_validator("readableText")
    @classmethod
    def require_readable_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be empty")
        return value


@router.post("/sources/text", status_code=status.HTTP_201_CREATED)
async def ingest_text_source(
    request: Request,
    payload: TextSourceRequest,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    require_unlocked_user(request, payload.userId)
    try:
        source, artifacts, spans = ingest_text_content(
            runtime_db,
            user_id=payload.userId,
            content=payload.content,
            filename=payload.filename,
            title=payload.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    compile_run = _queue_compile_run(runtime_db, source=source) if payload.compile else None
    runtime_db.commit()
    return _source_response(source, artifacts, spans, compile_run=compile_run)


@router.post("/sources/markdown", status_code=status.HTTP_201_CREATED)
async def ingest_markdown_source(
    request: Request,
    payload: TextSourceRequest,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    require_unlocked_user(request, payload.userId)
    try:
        source, artifacts, spans = ingest_markdown_content(
            runtime_db,
            user_id=payload.userId,
            content=payload.content,
            filename=payload.filename,
            title=payload.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    compile_run = _queue_compile_run(runtime_db, source=source) if payload.compile else None
    runtime_db.commit()
    return _source_response(source, artifacts, spans, compile_run=compile_run)


@router.post("/sources/web-capture", status_code=status.HTTP_201_CREATED)
async def ingest_web_capture_source(
    request: Request,
    payload: WebCaptureRequest,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    require_unlocked_user(request, payload.userId)
    try:
        source, artifacts, spans = ingest_web_capture(
            runtime_db,
            user_id=payload.userId,
            url=payload.url,
            readable_text=payload.readableText,
            title=payload.title,
            canonical_url=payload.canonicalUrl,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    compile_run = _queue_compile_run(runtime_db, source=source) if payload.compile else None
    runtime_db.commit()
    return _source_response(source, artifacts, spans, compile_run=compile_run)


@router.get("/sources/{source_id}")
async def get_source(
    request: Request,
    source_id: int,
    userId: int,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    require_unlocked_user(request, userId)
    source = _owned_source(runtime_db, user_id=userId, source_id=source_id)
    return _source_response(
        source,
        _source_artifacts(runtime_db, source_id=source.id),
        _source_spans(runtime_db, source_id=source.id),
    )


@router.get("/concepts/{concept_id}")
async def get_concept(
    request: Request,
    concept_id: int,
    userId: int,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    require_unlocked_user(request, userId)
    concept = runtime_db.scalar(
        select(RuntimeKnowledgeConcept).where(
            RuntimeKnowledgeConcept.id == concept_id,
            RuntimeKnowledgeConcept.user_id == userId,
        )
    )
    if concept is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Concept not found.")
    return {
        "id": concept.id,
        "slug": concept.slug,
        "title": concept.title,
        "conceptType": concept.concept_type,
        "bodyMarkdown": concept.body_markdown,
        "frontmatter": concept.frontmatter_json,
        "metadata": concept.metadata_json,
        "status": concept.status,
    }


@router.post("/sources/{source_id}/compile", status_code=status.HTTP_202_ACCEPTED)
async def compile_source(
    request: Request,
    source_id: int,
    userId: int,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    require_unlocked_user(request, userId)
    source = _owned_source(runtime_db, user_id=userId, source_id=source_id)
    run = _queue_compile_run(runtime_db, source=source)
    runtime_db.commit()
    return {"compileRun": _run_response(run)}


def _queue_compile_run(
    runtime_db: Session,
    *,
    source: RuntimeSource,
) -> RuntimeKnowledgeBundleRun:
    run = start_bundle_run(
        runtime_db,
        user_id=source.user_id,
        run_type="compiler:queued",
        source_id=source.id,
        input_json={"source_id": source.id},
    )
    return complete_bundle_run(
        runtime_db,
        run=run,
        result_json={"queued": True, "source_id": source.id},
    )


def _owned_source(runtime_db: Session, *, user_id: int, source_id: int) -> RuntimeSource:
    source = runtime_db.scalar(
        select(RuntimeSource).where(
            RuntimeSource.id == source_id,
            RuntimeSource.user_id == user_id,
        )
    )
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")
    return source


def _source_response(
    source: RuntimeSource,
    artifacts: list[RuntimeSourceArtifact],
    spans: list[RuntimeSourceSpan],
    *,
    compile_run: RuntimeKnowledgeBundleRun | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "source": {
            "id": source.id,
            "kind": source.kind,
            "sourceUri": source.source_uri,
            "contentHash": source.content_hash,
            "title": source.title,
            "mediaType": source.media_type,
            "status": source.status,
            "metadata": source.metadata_json,
        },
        "artifacts": [
            {
                "id": artifact.id,
                "artifactKind": artifact.artifact_kind,
                "contentHash": artifact.content_hash,
                "metadata": artifact.metadata_json,
            }
            for artifact in artifacts
        ],
        "spans": [
            {
                "id": span.id,
                "spanKind": span.span_kind,
                "locator": span.locator_json,
                "contentText": span.content_text,
                "contentHash": span.content_hash,
                "metadata": span.metadata_json,
            }
            for span in spans
        ],
    }
    if compile_run is not None:
        response["compileRun"] = _run_response(compile_run)
    return response


def _run_response(run: RuntimeKnowledgeBundleRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "runType": run.run_type,
        "sourceId": run.source_id,
    }


def _source_artifacts(runtime_db: Session, *, source_id: int) -> list[RuntimeSourceArtifact]:
    return list(
        runtime_db.scalars(
            select(RuntimeSourceArtifact)
            .where(RuntimeSourceArtifact.source_id == source_id)
            .order_by(RuntimeSourceArtifact.id)
        ).all()
    )


def _source_spans(runtime_db: Session, *, source_id: int) -> list[RuntimeSourceSpan]:
    return list(
        runtime_db.scalars(
            select(RuntimeSourceSpan)
            .where(RuntimeSourceSpan.source_id == source_id)
            .order_by(RuntimeSourceSpan.id)
        ).all()
    )
