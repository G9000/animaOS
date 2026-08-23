from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from stat import S_IFDIR, S_IFMT, S_IFREG
from typing import Any
from urllib.parse import urlparse

import yaml
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user_async
from anima_server.config import settings
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
from anima_server.services.corefs.asset_authority import asset_authority_selection
from anima_server.services.corefs.asset_mutations import AssetMutationError
from anima_server.services.corefs.diary_migration import migration_opaque_id
from anima_server.services.corefs.knowledge_authority import (
    CanonicalKnowledgeDocument,
    canonical_knowledge_document,
    canonical_knowledge_view,
    publish_canonical_knowledge_source,
)
from anima_server.services.ingestion.adapters.text import (
    ingest_markdown_content,
    ingest_text_content,
)
from anima_server.services.ingestion.adapters.web import (
    ingest_html_content,
    ingest_web_capture,
    reextract_source_html,
)
from anima_server.services.ingestion.document_compiler import (
    compile_source_knowledge_auto,
)
from anima_server.services.ingestion.html_extract import extract_html_article
from anima_server.services.ingestion.lint import lint_knowledge_bundle
from anima_server.services.ingestion.okf import (
    PortableOKFConcept,
    export_okf_bundle,
    export_portable_okf_bundle,
    import_okf_bundle,
    read_portable_okf_bundle,
)
from anima_server.services.ingestion.structured import parse_markdown_structure
from anima_server.services.ingestion.web_fetch import (
    UnsafeFetchUrlError,
    WebFetchDisabledError,
    WebFetchError,
    fetch_capture_html,
)

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
    readableText: str | None = Field(default=None)
    html: str | None = Field(default=None)
    fetch: bool = False
    title: str | None = Field(default=None, max_length=512)
    canonicalUrl: str | None = Field(default=None, max_length=2048)
    compile: bool = False

    @model_validator(mode="after")
    def require_exactly_one_input(self) -> WebCaptureRequest:
        provided = sum(
            (self.readableText is not None, self.html is not None, self.fetch)
        )
        if provided != 1:
            raise ValueError(
                "provide exactly one of readableText, html, or fetch=true"
            )
        for value in (self.readableText, self.html):
            if value is not None and not value.strip():
                raise ValueError("content must not be empty")
        return self


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
    await require_unlocked_user_async(request, userId)
    safe_limit = min(max(limit, 1), 200)
    corefs_index = _active_knowledge_index(userId)
    if corefs_index is not None:
        by_source: dict[int, Any] = {}
        for projection in corefs_index.knowledge_source_projections():
            by_source.setdefault(projection.source_id, projection)
        return {
            "sources": [
                _corefs_source_summary(projection)
                for _, projection in sorted(by_source.items(), reverse=True)[:safe_limit]
            ]
        }
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
    session = await require_unlocked_user_async(request, payload.userId)
    if len(payload.content.encode("utf-8")) > settings.diary_attachment_max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Text source is too large.",
        )
    if asset_authority_selection(session) is not None:
        projection = publish_canonical_knowledge_source(
            session=session,
            document=_canonical_text_document(payload, kind="text"),
        )
        return _canonical_source_write_response(
            projection,
            compile_requested=payload.compile,
        )
    _require_legacy_knowledge_mutation_allowed(payload.userId)
    try:
        source, artifacts, spans = ingest_text_content(
            runtime_db,
            user_id=payload.userId,
            content=payload.content,
            filename=payload.filename,
            title=payload.title,
            embedding_fn=generate_embedding,
            compile_knowledge=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    compile_run = (
        await _existing_or_new_compile_run(runtime_db, source=source, spans=spans)
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
    session = await require_unlocked_user_async(request, payload.userId)
    if len(payload.content.encode("utf-8")) > settings.diary_attachment_max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Markdown source is too large.",
        )
    if asset_authority_selection(session) is not None:
        projection = publish_canonical_knowledge_source(
            session=session,
            document=_canonical_text_document(payload, kind="markdown"),
        )
        return _canonical_source_write_response(
            projection,
            compile_requested=payload.compile,
        )
    _require_legacy_knowledge_mutation_allowed(payload.userId)
    try:
        source, artifacts, spans = ingest_markdown_content(
            runtime_db,
            user_id=payload.userId,
            content=payload.content,
            filename=payload.filename,
            title=payload.title,
            embedding_fn=generate_embedding,
            compile_knowledge=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    compile_run = (
        await _existing_or_new_compile_run(runtime_db, source=source, spans=spans)
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
    session = await require_unlocked_user_async(request, payload.userId)
    url = payload.url
    html = payload.html
    if payload.fetch:
        try:
            url, html = fetch_capture_html(payload.url)
        except WebFetchDisabledError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except (UnsafeFetchUrlError, WebFetchError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        # Redirects can land on a URL longer than the request model (and
        # the source_uri column) allow; revalidate so this stays a 422
        # instead of a database length error.
        if len(url) > SOURCE_URI_MAX_LENGTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "The fetched page redirected to a URL longer than "
                    f"{SOURCE_URI_MAX_LENGTH} characters."
                ),
            )
    if html is not None and len(html.encode("utf-8")) > settings.diary_attachment_max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "HTML content is too large. "
                f"Limit is {settings.diary_attachment_max_size_bytes} bytes."
            ),
        )
    if asset_authority_selection(session) is not None:
        document = _canonical_web_document(
            url=url,
            readable_text=payload.readableText,
            html=html,
            title=payload.title,
        )
        projection = publish_canonical_knowledge_source(
            session=session,
            document=document,
        )
        return _canonical_source_write_response(
            projection,
            compile_requested=payload.compile,
        )
    _require_legacy_knowledge_mutation_allowed(payload.userId)
    try:
        source, artifacts, spans = ingest_web_capture(
            runtime_db,
            user_id=payload.userId,
            url=url,
            readable_text=payload.readableText,
            html=html,
            title=payload.title,
            canonical_url=payload.canonicalUrl,
            embedding_fn=generate_embedding,
            compile_knowledge=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    compile_run = (
        await _existing_or_new_compile_run(runtime_db, source=source, spans=spans)
        if payload.compile
        else None
    )
    runtime_db.commit()
    return _source_response(source, artifacts, spans, compile_run=compile_run)


_HTML_UPLOAD_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_HTML_UPLOAD_FALLBACK_CONTENT_TYPES = frozenset({"", "application/octet-stream"})


@router.post("/sources/html", status_code=status.HTTP_201_CREATED)
async def ingest_html_source(
    request: Request,
    userId: int = Form(..., ge=0),
    title: str | None = Form(default=None, max_length=512),
    compileKnowledge: bool = Form(default=False, alias="compile"),
    file: UploadFile = File(...),
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    session = await require_unlocked_user_async(request, userId)
    filename = file.filename or "page.html"
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    has_html_extension = filename.lower().endswith((".html", ".htm"))
    if content_type not in _HTML_UPLOAD_CONTENT_TYPES and not (
        content_type in _HTML_UPLOAD_FALLBACK_CONTENT_TYPES and has_html_extension
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only HTML uploads are supported.",
        )

    data = await file.read(settings.diary_attachment_max_size_bytes + 1)
    if not data.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="HTML file must not be empty.",
        )
    if len(data) > settings.diary_attachment_max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                "HTML file is too large. "
                f"Limit is {settings.diary_attachment_max_size_bytes} bytes."
            ),
        )

    if asset_authority_selection(session) is not None:
        try:
            html = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="HTML upload must be valid UTF-8.",
            ) from exc
        projection = publish_canonical_knowledge_source(
            session=session,
            document=_canonical_html_document(
                html=html,
                filename=filename,
                title=title,
            ),
        )
        return _canonical_source_write_response(
            projection,
            compile_requested=compileKnowledge,
        )
    _require_legacy_knowledge_mutation_allowed(userId)

    try:
        source, artifacts, spans = ingest_html_content(
            runtime_db,
            user_id=userId,
            html=data.decode("utf-8", errors="replace"),
            filename=filename,
            title=title,
            embedding_fn=generate_embedding,
            compile_knowledge=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    compile_run = (
        await _existing_or_new_compile_run(runtime_db, source=source, spans=spans)
        if compileKnowledge
        else None
    )
    runtime_db.commit()
    return _source_response(source, artifacts, spans, compile_run=compile_run)


@router.post("/sources/{source_id}/reextract")
async def reextract_source(
    request: Request,
    source_id: int,
    userId: int,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    session = await require_unlocked_user_async(request, userId)
    if asset_authority_selection(session) is not None:
        selected = canonical_knowledge_document(
            session=session,
            source_id=source_id,
        )
        if selected is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Source not found.",
            )
        stable_id, document = selected
        if document.source_media_type != "text/html":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only captured HTML sources can be re-extracted.",
            )
        extraction_url = (
            document.source_uri
            if document.source_uri.startswith(("http://", "https://"))
            else None
        )
        extraction = extract_html_article(
            document.original_content,
            url=extraction_url,
        )
        refreshed = CanonicalKnowledgeDocument(
            source_kind=document.source_kind,
            source_uri=document.source_uri,
            source_title=document.source_title or extraction.title,
            source_media_type=document.source_media_type,
            filename=document.filename,
            artifact_kind="structured_markdown",
            content=parse_markdown_structure(extraction.markdown).to_markdown(),
            original_content=document.original_content,
        )
        projection = publish_canonical_knowledge_source(
            session=session,
            document=refreshed,
            stable_id=stable_id,
            replace_existing=True,
        )
        return _canonical_source_write_response(
            projection,
            compile_requested=True,
        )
    _require_legacy_knowledge_mutation_allowed(userId)
    # Checked before re-extraction: replacing spans cascades citation rows,
    # so an already-compiled source must be recompiled afterwards or its
    # concepts would go stale/orphaned until a manual compile.
    had_compiled_concepts = (
        runtime_db.scalar(
            select(RuntimeKnowledgeConceptSource.id)
            .where(
                RuntimeKnowledgeConceptSource.user_id == userId,
                RuntimeKnowledgeConceptSource.source_id == source_id,
            )
            .limit(1)
        )
        is not None
    )
    try:
        source, artifacts, spans = reextract_source_html(
            runtime_db,
            user_id=userId,
            source_id=source_id,
            embedding_fn=generate_embedding,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    compile_run = None
    if had_compiled_concepts:
        result = await compile_source_knowledge_auto(
            runtime_db,
            source=source,
            spans=spans,
            embedding_fn=generate_embedding,
            mode="refresh",
        )
        if result.status != "completed":
            # Replacing spans cascaded the old citations; committing now
            # would leave active concepts with no citations. Keep the
            # previous span/citation state instead.
            runtime_db.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Re-extraction was rolled back: the refresh compile "
                    "failed, so the previous spans and citations were kept. "
                    "Retry when the compiler model is available."
                ),
            )
        compile_run = runtime_db.get(RuntimeKnowledgeBundleRun, result.run_id)
    runtime_db.commit()
    return _source_response(source, artifacts, spans, compile_run=compile_run)


@router.get("/sources/{source_id}")
async def get_source(
    request: Request,
    source_id: int,
    userId: int,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    await require_unlocked_user_async(request, userId)
    corefs_index = _active_knowledge_index(userId)
    if corefs_index is not None:
        projections = tuple(
            item
            for item in corefs_index.knowledge_source_projections()
            if item.source_id == source_id
        )
        if not projections:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Source not found.",
            )
        return _corefs_source_response(projections)
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
    includeRetired: bool = False,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    await require_unlocked_user_async(request, userId)
    corefs_index = _active_knowledge_index(userId)
    if corefs_index is not None:
        safe_limit = min(max(limit, 1), 200)
        return {
            "concepts": [
                _corefs_concept_summary(concept)
                for concept in corefs_index.knowledge_concept_projections()[:safe_limit]
            ]
        }
    safe_limit = min(max(limit, 1), 200)
    stmt = select(RuntimeKnowledgeConcept).where(RuntimeKnowledgeConcept.user_id == userId)
    if not includeRetired:
        # A refresh compile retires superseded pages as "inactive". Retrieval
        # and lint scope to "active"; the library listing must match, or the
        # wiki keeps showing pages its sources no longer support.
        stmt = stmt.where(RuntimeKnowledgeConcept.status == "active")
    concepts = list(
        runtime_db.scalars(
            stmt.order_by(
                RuntimeKnowledgeConcept.updated_at.desc(),
                RuntimeKnowledgeConcept.id.desc(),
            ).limit(safe_limit)
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
    await require_unlocked_user_async(request, userId)
    corefs_index = _active_knowledge_index(userId)
    if corefs_index is not None:
        concept = corefs_index.knowledge_concept_projection(concept_id)
        if concept is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Concept not found.",
            )
        return _corefs_concept_response(concept)
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
        "description": concept.description,
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
    session = await require_unlocked_user_async(request, userId)
    if asset_authority_selection(session) is not None:
        projection = next(
            (
                item
                for item in canonical_knowledge_view(
                    session=session
                ).knowledge_source_projections()
                if item.source_id == source_id
            ),
            None,
        )
        if projection is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Source not found.",
            )
        return {"compileRun": _canonical_compile_run(projection)}
    _require_legacy_knowledge_mutation_allowed(userId)
    source = _owned_source(runtime_db, user_id=userId, source_id=source_id)
    run = await _compile_source_now(
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
    await require_unlocked_user_async(request, userId)
    query = q.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="q must not be empty.",
        )
    safe_limit = min(max(limit, 1), 50)
    corefs_index = _active_knowledge_index(userId)
    if corefs_index is not None:
        projections = corefs_index.search_knowledge_source_projections(
            query,
            limit=safe_limit,
        )
        return {
            "query": query,
            "concepts": [
                _corefs_concept_summary(concept)
                for concept in corefs_index.search_knowledge_concept_projections(
                    query,
                    limit=safe_limit,
                )
            ],
            "evidenceSpans": [
                _corefs_evidence_response(projection)
                for projection in projections
            ],
        }
    lowered = query.lower()
    concepts = [
        _concept_summary(concept)
        for concept in runtime_db.scalars(
            select(RuntimeKnowledgeConcept)
            .where(
                RuntimeKnowledgeConcept.user_id == userId,
                # Retired pages are excluded from every retrieval path; search
                # must match or a refresh compile's superseded pages keep
                # surfacing as live results.
                RuntimeKnowledgeConcept.status == "active",
            )
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
            .where(
                RuntimeSourceSpan.user_id == userId,
                RuntimeSource.user_id == userId,
                # Section spans are parent read units, not evidence — the
                # retrieval paths exclude them and search must match.
                RuntimeSourceSpan.span_kind != "section",
            )
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
    session = await require_unlocked_user_async(request, userId)
    with tempfile.TemporaryDirectory(prefix="anima-okf-export-") as temp_dir:
        bundle_dir = Path(temp_dir) / "bundle"
        if asset_authority_selection(session) is not None:
            concepts = canonical_knowledge_view(
                session=session
            ).knowledge_concept_projections()
            export_portable_okf_bundle(
                concepts=tuple(
                    PortableOKFConcept(
                        slug=concept.slug,
                        concept_type=concept.concept_type,
                        title=concept.title,
                        description=concept.description,
                        body_markdown=concept.body_markdown,
                        frontmatter_json={
                            "anima": {
                                "source_id": concept.source_id,
                                "derived": True,
                            }
                        },
                        original_markdown="",
                        linked_slugs=(),
                    )
                    for concept in concepts
                ),
                bundle_dir=bundle_dir,
            )
        else:
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
    session = await require_unlocked_user_async(request, userId)
    content = await file.read(settings.diary_attachment_max_size_bytes + 1)
    if len(content) > settings.diary_attachment_max_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="OKF bundle is too large.",
        )
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
        if asset_authority_selection(session) is not None:
            try:
                concepts = read_portable_okf_bundle(bundle_dir=bundle_dir)
                for concept in concepts:
                    publish_canonical_knowledge_source(
                        session=session,
                        stable_id=migration_opaque_id(
                            "knowledge-okf-source",
                            concept.slug,
                        ),
                        replace_existing=True,
                        document=CanonicalKnowledgeDocument(
                            source_kind="okf",
                            source_uri=f"okf://{concept.slug}.md",
                            source_title=concept.title,
                            source_media_type="text/markdown",
                            filename=f"{concept.slug}.md",
                            artifact_kind="structured_markdown",
                            content=concept.body_markdown,
                            original_content=concept.original_markdown,
                        ),
                    )
            except (ValueError, yaml.YAMLError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Invalid OKF bundle contents.",
                ) from exc
            except AssetMutationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Canonical OKF import failed.",
                ) from exc
            imported_slugs = {concept.slug for concept in concepts}
            link_count = sum(
                1
                for concept in concepts
                for linked_slug in set(concept.linked_slugs)
                if linked_slug in imported_slugs and linked_slug != concept.slug
            )
            return {"conceptCount": len(concepts), "linkCount": link_count}
        _require_legacy_knowledge_mutation_allowed(userId)
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
    session = await require_unlocked_user_async(request, payload.userId)
    if asset_authority_selection(session) is not None:
        return {
            "findings": _canonical_lint_findings(
                session=session,
                source_id=payload.sourceId,
                concept_id=payload.conceptId,
            )
        }
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


async def _compile_source_now(
    runtime_db: Session,
    *,
    source: RuntimeSource,
    spans: list[RuntimeSourceSpan],
) -> RuntimeKnowledgeBundleRun:
    result = await compile_source_knowledge_auto(
        runtime_db,
        source=source,
        spans=spans,
        embedding_fn=generate_embedding,
    )
    run = runtime_db.get(RuntimeKnowledgeBundleRun, result.run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Compile run was not persisted.",
    )
    return run


async def _existing_or_new_compile_run(
    runtime_db: Session,
    *,
    source: RuntimeSource,
    spans: list[RuntimeSourceSpan],
) -> RuntimeKnowledgeBundleRun:
    existing = _latest_source_compile_run(runtime_db, source=source)
    if existing is not None:
        return existing
    return await _compile_source_now(
        runtime_db,
        source=source,
        spans=spans,
    )


def _latest_source_compile_run(
    runtime_db: Session,
    *,
    source: RuntimeSource,
) -> RuntimeKnowledgeBundleRun | None:
    # Only successful or in-flight runs short-circuit a compile request;
    # a failed run must not make transient compiler failures sticky when
    # the user explicitly re-ingests with compile=true.
    return runtime_db.scalar(
        select(RuntimeKnowledgeBundleRun)
        .where(
            RuntimeKnowledgeBundleRun.user_id == source.user_id,
            RuntimeKnowledgeBundleRun.source_id == source.id,
            RuntimeKnowledgeBundleRun.run_type.like("compile:%"),
            RuntimeKnowledgeBundleRun.status.in_(("pending", "running", "completed")),
        )
        .order_by(RuntimeKnowledgeBundleRun.id.desc())
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


def _active_knowledge_index(user_id: int) -> Any | None:
    from anima_server.services.corefs.asset_authority import (
        canonical_asset_session_or_legacy,
    )

    session = canonical_asset_session_or_legacy(user_id)
    return canonical_knowledge_view(session=session) if session is not None else None


def _canonical_text_document(
    payload: TextSourceRequest,
    *,
    kind: str,
) -> CanonicalKnowledgeDocument:
    content = payload.content.strip()
    default_name = "knowledge.md" if kind == "markdown" else "knowledge.txt"
    filename = _canonical_source_filename(payload.filename, default=default_name)
    normalized = (
        parse_markdown_structure(content).to_markdown()
        if kind == "markdown"
        else content
    )
    return CanonicalKnowledgeDocument(
        source_kind=kind,
        source_uri=f"{kind}://{filename}",
        source_title=payload.title or filename,
        source_media_type="text/markdown" if kind == "markdown" else "text/plain",
        filename=filename,
        artifact_kind="structured_markdown" if kind == "markdown" else "plain_text",
        content=normalized,
        original_content=content,
    )


def _canonical_web_document(
    *,
    url: str,
    readable_text: str | None,
    html: str | None,
    title: str | None,
) -> CanonicalKnowledgeDocument:
    source_uri = _canonical_http_url(url)
    if html is None:
        content = (readable_text or "").strip()
        return CanonicalKnowledgeDocument(
            source_kind="web_capture",
            source_uri=source_uri,
            source_title=title,
            source_media_type="text/plain",
            filename="web-capture.txt",
            artifact_kind="readable_text",
            content=content,
            original_content=content,
        )
    extraction = extract_html_article(html.strip(), url=source_uri)
    markdown = parse_markdown_structure(extraction.markdown).to_markdown()
    return CanonicalKnowledgeDocument(
        source_kind="web_capture",
        source_uri=source_uri,
        source_title=title or extraction.title,
        source_media_type="text/html",
        filename="web-capture.html",
        artifact_kind="structured_markdown",
        content=markdown,
        original_content=html.strip(),
    )


def _canonical_html_document(
    *,
    html: str,
    filename: str,
    title: str | None,
) -> CanonicalKnowledgeDocument:
    safe_name = _canonical_source_filename(filename, default="page.html")
    original = html.strip()
    extraction = extract_html_article(original)
    markdown = parse_markdown_structure(extraction.markdown).to_markdown()
    return CanonicalKnowledgeDocument(
        source_kind="html",
        source_uri=f"html://{safe_name}",
        source_title=title or extraction.title or safe_name,
        source_media_type="text/html",
        filename=safe_name,
        artifact_kind="structured_markdown",
        content=markdown,
        original_content=original,
    )


def _canonical_source_filename(value: str | None, *, default: str) -> str:
    candidate = Path(value or default).name.strip()
    if not candidate:
        return default
    return candidate[:255]


def _canonical_http_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(
        character.isspace() for character in normalized
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="url must be an absolute http(s) URL",
        )
    return normalized


def _canonical_source_write_response(
    projection: Any,
    *,
    compile_requested: bool,
) -> dict[str, Any]:
    response = _corefs_source_response((projection,))
    if compile_requested:
        response["compileRun"] = _canonical_compile_run(projection)
    return response


def _canonical_compile_run(projection: Any) -> dict[str, Any]:
    return {
        "id": projection.source_id,
        "status": "completed",
        "runType": "compile:derived",
        "sourceId": projection.source_id,
    }


def _canonical_lint_findings(
    *,
    session: Any,
    source_id: int | None,
    concept_id: int | None,
) -> list[dict[str, Any]]:
    view = canonical_knowledge_view(session=session)
    concepts = tuple(
        concept
        for concept in view.knowledge_concept_projections()
        if (source_id is None or concept.source_id == source_id)
        and (concept_id is None or concept.concept_id == concept_id)
    )
    title_counts: dict[str, int] = {}
    for concept in view.knowledge_concept_projections():
        title_counts[concept.title] = title_counts.get(concept.title, 0) + 1
    return [
        {
            "code": "duplicate_concept_title",
            "severity": "warning",
            "message": f"Concept title {concept.title!r} is duplicated.",
            "conceptId": concept.concept_id,
            "sourceId": concept.source_id,
            "linkId": None,
        }
        for concept in concepts
        if title_counts[concept.title] > 1
    ]


def _require_legacy_knowledge_mutation_allowed(user_id: int) -> None:
    from anima_server.services.corefs.asset_authority import (
        require_legacy_asset_mutation_allowed,
    )

    require_legacy_asset_mutation_allowed(user_id)


def _corefs_source_summary(projection: Any) -> dict[str, Any]:
    return {
        "id": projection.source_id,
        "kind": projection.source_kind,
        "sourceUri": projection.source_uri,
        "contentHash": projection.content_sha256,
        "title": projection.source_title,
        "mediaType": projection.source_media_type,
        "status": "indexed",
        "metadata": {"authority": "corefs"},
    }


def _corefs_source_response(projections: tuple[Any, ...]) -> dict[str, Any]:
    source = projections[0]
    return {
        "source": _corefs_source_summary(source),
        "artifacts": [
            {
                "id": item.artifact_id,
                "sourceId": item.source_id,
                "artifactKind": item.artifact_kind,
                "contentText": item.content_text,
                "contentHash": item.content_sha256,
                "metadata": {
                    "authority": "corefs",
                    "objectUri": f"corefs://object/{item.stable_id}",
                },
            }
            for item in projections
        ],
        "spans": [_corefs_evidence_response(item) for item in projections],
    }


def _corefs_evidence_response(projection: Any) -> dict[str, Any]:
    return {
        "id": projection.artifact_id,
        "sourceId": projection.source_id,
        "sourceTitle": projection.source_title,
        "sourceUri": projection.source_uri,
        "spanKind": projection.artifact_kind,
        "locator": {"corefsObjectId": projection.stable_id},
        "contentText": projection.content_text,
        "metadata": {"authority": "corefs"},
    }


def _corefs_concept_summary(concept: Any) -> dict[str, Any]:
    return {
        "id": concept.concept_id,
        "slug": concept.slug,
        "title": concept.title,
        "description": concept.description,
        "conceptType": concept.concept_type,
        "status": "active",
        "metadata": {"authority": "corefs", "derived": True},
    }


def _corefs_concept_response(concept: Any) -> dict[str, Any]:
    return {
        **_corefs_concept_summary(concept),
        "bodyMarkdown": concept.body_markdown,
        "frontmatter": {
            "type": concept.concept_type,
            "title": concept.title,
            "anima": {"source_id": concept.source_id, "derived": True},
        },
        "citations": [
            {
                "id": concept.artifact_id,
                "sourceId": concept.source_id,
                "spanId": concept.artifact_id,
                "citationLabel": concept.title,
                "quoteText": concept.body_markdown,
                "sourceTitle": concept.title,
                "sourceUri": concept.source_uri,
                "spanKind": concept.artifact_kind,
                "locator": {"corefsObjectId": concept.stable_id},
                "contentText": concept.body_markdown,
                "metadata": {"authority": "corefs", "derived": True},
            }
        ],
        "links": [],
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
    members = archive.infolist()
    if len(members) > 1_000 or sum(member.file_size for member in members) > (
        settings.diary_attachment_max_size_bytes
    ):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Expanded OKF bundle is too large.",
        )
    for member in members:
        destination = (target_root / member.filename).resolve()
        unix_mode = member.external_attr >> 16
        unsafe_type = unix_mode and S_IFMT(unix_mode) not in {0, S_IFDIR, S_IFREG}
        if (
            (target_root not in destination.parents and destination != target_root)
            or member.flag_bits & 0x1
            or unsafe_type
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid OKF bundle path.",
            )
        archive.extract(member, target_root)
