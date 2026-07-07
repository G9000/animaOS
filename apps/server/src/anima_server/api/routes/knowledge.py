from __future__ import annotations

import json
import re
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user
from anima_server.db import get_runtime_db
from anima_server.models.runtime import (
    RuntimeKnowledgeBundleRun,
    RuntimeKnowledgeConcept,
    RuntimeKnowledgeConceptSource,
    RuntimeKnowledgeLink,
    RuntimeSource,
    RuntimeSourceArtifact,
    RuntimeSourceSpan,
)
from anima_server.services.agent.embeddings import generate_embedding
from anima_server.services.ingestion.adapters.text import (
    ingest_markdown_content,
    ingest_text_content,
)
from anima_server.services.ingestion.adapters.web import ingest_web_capture
from anima_server.services.ingestion.compiler import (
    CompilerRequest,
    compile_source_to_concepts,
)
from anima_server.services.ingestion.lint import lint_knowledge_bundle
from anima_server.services.ingestion.okf import export_okf_bundle, import_okf_bundle

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
SOURCE_URI_MAX_LENGTH = 1024


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
    url: str = Field(min_length=1, max_length=SOURCE_URI_MAX_LENGTH)
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


class KnowledgeLintRequest(BaseModel):
    userId: int = Field(ge=0)
    sourceId: int | None = Field(default=None, ge=1)
    conceptId: int | None = Field(default=None, ge=1)


@router.get("/sources")
async def list_sources(
    request: Request,
    userId: int,
    limit: int = 50,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    require_unlocked_user(request, userId)
    safe_limit = min(max(limit, 1), 200)
    sources = list(
        runtime_db.scalars(
            select(RuntimeSource)
            .where(RuntimeSource.user_id == userId)
            .order_by(RuntimeSource.created_at.desc(), RuntimeSource.id.desc())
            .limit(safe_limit)
        ).all()
    )
    return {"sources": [_source_summary(source) for source in sources]}


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
            embedding_fn=generate_embedding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    compile_run = (
        _compile_source_now(runtime_db, source=source, spans=spans)
        if payload.compile
        else None
    )
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
            embedding_fn=generate_embedding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    compile_run = (
        _compile_source_now(runtime_db, source=source, spans=spans)
        if payload.compile
        else None
    )
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
            embedding_fn=generate_embedding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    compile_run = (
        _compile_source_now(runtime_db, source=source, spans=spans)
        if payload.compile
        else None
    )
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


@router.get("/concepts")
async def list_concepts(
    request: Request,
    userId: int,
    limit: int = 50,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    require_unlocked_user(request, userId)
    safe_limit = min(max(limit, 1), 200)
    concepts = list(
        runtime_db.scalars(
            select(RuntimeKnowledgeConcept)
            .where(RuntimeKnowledgeConcept.user_id == userId)
            .order_by(
                RuntimeKnowledgeConcept.updated_at.desc(),
                RuntimeKnowledgeConcept.id.desc(),
            )
            .limit(safe_limit)
        ).all()
    )
    return {"concepts": [_concept_summary(concept) for concept in concepts]}


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
        "citations": _concept_citations(
            runtime_db,
            user_id=userId,
            concept_id=concept.id,
        ),
        "links": _concept_links(runtime_db, user_id=userId, concept_id=concept.id),
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
    run = _compile_source_now(
        runtime_db,
        source=source,
        spans=_source_spans(runtime_db, source_id=source.id),
    )
    runtime_db.commit()
    return {"compileRun": _run_response(run)}


@router.get("/search")
async def search_knowledge(
    request: Request,
    userId: int,
    q: str,
    limit: int = 20,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    require_unlocked_user(request, userId)
    query = q.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="q must not be empty.",
        )
    safe_limit = min(max(limit, 1), 50)
    lowered = query.lower()
    concepts = [
        _concept_summary(concept)
        for concept in runtime_db.scalars(
            select(RuntimeKnowledgeConcept)
            .where(RuntimeKnowledgeConcept.user_id == userId)
            .order_by(
                RuntimeKnowledgeConcept.updated_at.desc(),
                RuntimeKnowledgeConcept.id.desc(),
            )
        ).all()
        if _contains_text(
            lowered,
            concept.title,
            concept.description,
            concept.body_markdown,
            concept.slug,
            concept.concept_type,
        )
    ][:safe_limit]
    spans = [
        _span_search_response(span, source)
        for span, source in runtime_db.execute(
            select(RuntimeSourceSpan, RuntimeSource)
            .join(RuntimeSource, RuntimeSourceSpan.source_id == RuntimeSource.id)
            .where(RuntimeSourceSpan.user_id == userId, RuntimeSource.user_id == userId)
            .order_by(RuntimeSourceSpan.created_at.desc(), RuntimeSourceSpan.id.desc())
        ).all()
        if _contains_text(
            lowered,
            span.content_text,
            span.span_kind,
            source.title,
            source.source_uri,
        )
    ][:safe_limit]
    return {"query": query, "concepts": concepts, "evidenceSpans": spans}


@router.get("/export")
async def export_knowledge(
    request: Request,
    userId: int,
    runtime_db: Session = Depends(get_runtime_db),
) -> Response:
    require_unlocked_user(request, userId)
    with tempfile.TemporaryDirectory(prefix="anima-okf-export-") as temp_dir:
        bundle_dir = Path(temp_dir) / "bundle"
        export_okf_bundle(runtime_db, user_id=userId, bundle_dir=bundle_dir)
        archive_path = Path(temp_dir) / "knowledge-bundle.zip"
        with zipfile.ZipFile(
            archive_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for path in sorted(bundle_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(bundle_dir).as_posix())
        return Response(
            content=archive_path.read_bytes(),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="knowledge-bundle.zip"',
            },
        )


@router.post("/import", status_code=status.HTTP_201_CREATED)
async def import_knowledge(
    request: Request,
    userId: int,
    file: UploadFile = File(...),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    require_unlocked_user(request, userId)
    content = await file.read()
    with tempfile.TemporaryDirectory(prefix="anima-okf-import-") as temp_dir:
        bundle_dir = Path(temp_dir) / "bundle"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        try:
            archive_path = Path(temp_dir) / "bundle.zip"
            archive_path.write_bytes(content)
            with zipfile.ZipFile(archive_path) as archive:
                _extract_zip_safely(archive, bundle_dir)
        except zipfile.BadZipFile as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid OKF bundle zip.",
            ) from exc
        try:
            result = import_okf_bundle(runtime_db, user_id=userId, bundle_dir=bundle_dir)
        except (ValueError, yaml.YAMLError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid OKF bundle contents.",
            ) from exc
        runtime_db.commit()
    return {"conceptCount": result.concept_count, "linkCount": result.link_count}


@router.post("/lint")
async def lint_knowledge(
    request: Request,
    payload: KnowledgeLintRequest,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    require_unlocked_user(request, payload.userId)
    findings = lint_knowledge_bundle(
        runtime_db,
        user_id=payload.userId,
        source_id=payload.sourceId,
        concept_id=payload.conceptId,
    )
    return {
        "findings": [
            {
                "code": finding.code,
                "severity": finding.severity,
                "message": finding.message,
                "conceptId": finding.concept_id,
                "sourceId": finding.source_id,
                "linkId": finding.link_id,
            }
            for finding in findings
        ]
    }


def _compile_source_now(
    runtime_db: Session,
    *,
    source: RuntimeSource,
    spans: list[RuntimeSourceSpan],
) -> RuntimeKnowledgeBundleRun:
    result = compile_source_to_concepts(
        runtime_db,
        user_id=source.user_id,
        source_id=source.id,
        span_ids=[span.id for span in spans],
        model=_compile_model,
        embedding_fn=generate_embedding,
    )
    run = runtime_db.get(RuntimeKnowledgeBundleRun, result.run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Compile run was not persisted.",
        )
    return run


def _compile_model(request: CompilerRequest) -> str:
    span_ids = [span.id for span in request.spans]
    title = request.source.title or request.source.source_uri or f"Source {request.source.id}"
    return json.dumps(
        {
            "concepts": [
                {
                    "type": "source_summary",
                    "slug": _source_summary_slug(request.source),
                    "title": title,
                    "description": f"Compiled source summary for {title}.",
                    "body_markdown": _source_summary_body(request.spans),
                    "source_span_ids": span_ids,
                    "tags": ["compiled", "source_summary"],
                }
            ],
            "links": [],
        },
        ensure_ascii=True,
    )


def _source_summary_slug(source: RuntimeSource) -> str:
    base = source.title or source.source_uri or f"source-{source.id}"
    normalized = re.sub(r"[^a-z0-9]+", "-", base.casefold()).strip("-")
    suffix = normalized[:180].strip("-") or "summary"
    return f"source-{source.id}-{suffix}"[:255].rstrip("-")


def _source_summary_body(spans: Sequence[RuntimeSourceSpan]) -> str:
    lines = [
        f"- {' '.join(span.content_text.split())}"
        for span in spans[:20]
        if span.content_text.strip()
    ]
    return "\n".join(lines) or "No source spans were available."


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


def _source_summary(source: RuntimeSource) -> dict[str, Any]:
    return {
        "id": source.id,
        "kind": source.kind,
        "sourceUri": source.source_uri,
        "contentHash": source.content_hash,
        "title": source.title,
        "mediaType": source.media_type,
        "status": source.status,
        "metadata": source.metadata_json,
    }


def _concept_summary(concept: RuntimeKnowledgeConcept) -> dict[str, Any]:
    return {
        "id": concept.id,
        "slug": concept.slug,
        "title": concept.title,
        "description": concept.description,
        "conceptType": concept.concept_type,
        "status": concept.status,
        "metadata": concept.metadata_json,
    }


def _concept_citations(
    runtime_db: Session,
    *,
    user_id: int,
    concept_id: int,
) -> list[dict[str, Any]]:
    rows = runtime_db.execute(
        select(RuntimeKnowledgeConceptSource, RuntimeSourceSpan, RuntimeSource)
        .join(
            RuntimeSourceSpan,
            RuntimeKnowledgeConceptSource.span_id == RuntimeSourceSpan.id,
        )
        .join(RuntimeSource, RuntimeKnowledgeConceptSource.source_id == RuntimeSource.id)
        .where(
            RuntimeKnowledgeConceptSource.user_id == user_id,
            RuntimeKnowledgeConceptSource.concept_id == concept_id,
            RuntimeSourceSpan.user_id == user_id,
            RuntimeSource.user_id == user_id,
        )
        .order_by(
            RuntimeKnowledgeConceptSource.created_at,
            RuntimeKnowledgeConceptSource.id,
        )
    ).all()
    return [
        {
            "id": link.id,
            "sourceId": source.id,
            "spanId": span.id,
            "citationLabel": link.citation_label,
            "quoteText": link.quote_text,
            "sourceTitle": source.title,
            "sourceUri": source.source_uri,
            "spanKind": span.span_kind,
            "locator": span.locator_json,
            "contentText": span.content_text,
            "metadata": link.metadata_json,
        }
        for link, span, source in rows
    ]


def _concept_links(
    runtime_db: Session,
    *,
    user_id: int,
    concept_id: int,
) -> list[dict[str, Any]]:
    links = runtime_db.scalars(
        select(RuntimeKnowledgeLink)
        .where(
            RuntimeKnowledgeLink.user_id == user_id,
            RuntimeKnowledgeLink.source_concept_id == concept_id,
        )
        .order_by(RuntimeKnowledgeLink.created_at, RuntimeKnowledgeLink.id)
    ).all()
    return [
        {
            "id": link.id,
            "sourceConceptId": link.source_concept_id,
            "targetConceptId": link.target_concept_id,
            "linkType": link.link_type,
            "confidence": link.confidence,
            "metadata": link.metadata_json,
        }
        for link in links
    ]


def _span_search_response(
    span: RuntimeSourceSpan,
    source: RuntimeSource,
) -> dict[str, Any]:
    return {
        "id": span.id,
        "sourceId": source.id,
        "sourceTitle": source.title,
        "sourceUri": source.source_uri,
        "spanKind": span.span_kind,
        "locator": span.locator_json,
        "contentText": span.content_text,
        "metadata": span.metadata_json,
    }


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


def _contains_text(needle: str, *values: str | None) -> bool:
    return any(needle in value.lower() for value in values if value)


def _extract_zip_safely(archive: zipfile.ZipFile, target_dir: Path) -> None:
    target_root = target_dir.resolve()
    for member in archive.infolist():
        destination = (target_root / member.filename).resolve()
        if target_root not in destination.parents and destination != target_root:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid OKF bundle path.",
            )
        archive.extract(member, target_root)
