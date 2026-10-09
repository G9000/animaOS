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
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_user_async
from anima_server.config import settings
from anima_server.db import get_runtime_db
from anima_server.services.corefs.asset_mutations import AssetMutationError
from anima_server.services.corefs.catalog_reader import stable_opaque_id
from anima_server.services.corefs.knowledge_authority import (
    CanonicalKnowledgeDocument,
    canonical_knowledge_document,
    canonical_knowledge_view,
    publish_canonical_knowledge_source,
)
from anima_server.services.ingestion.html_extract import extract_html_article
from anima_server.services.ingestion.okf import (
    PortableOKFConcept,
    export_portable_okf_bundle,
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
        provided = sum((self.readableText is not None, self.html is not None, self.fetch))
        if provided != 1:
            raise ValueError("provide exactly one of readableText, html, or fetch=true")
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
    by_source: dict[int, Any] = {}
    for projection in corefs_index.knowledge_source_projections():
        by_source.setdefault(projection.source_id, projection)
    return {
        "sources": [
            _corefs_source_summary(projection)
            for _, projection in sorted(by_source.items(), reverse=True)[:safe_limit]
        ]
    }


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
    projection = publish_canonical_knowledge_source(
        session=session,
        document=_canonical_text_document(payload, kind="text"),
    )
    return _canonical_source_write_response(
        projection,
        compile_requested=payload.compile,
    )


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
    projection = publish_canonical_knowledge_source(
        session=session,
        document=_canonical_text_document(payload, kind="markdown"),
    )
    return _canonical_source_write_response(
        projection,
        compile_requested=payload.compile,
    )


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
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
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


@router.post("/sources/{source_id}/reextract")
async def reextract_source(
    request: Request,
    source_id: int,
    userId: int,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    session = await require_unlocked_user_async(request, userId)
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
        document.source_uri if document.source_uri.startswith(("http://", "https://")) else None
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


@router.get("/sources/{source_id}")
async def get_source(
    request: Request,
    source_id: int,
    userId: int,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    await require_unlocked_user_async(request, userId)
    corefs_index = _active_knowledge_index(userId)
    projections = tuple(
        item for item in corefs_index.knowledge_source_projections() if item.source_id == source_id
    )
    if not projections:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source not found.",
        )
    return _corefs_source_response(projections)


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
    safe_limit = min(max(limit, 1), 200)
    return {
        "concepts": [
            _corefs_concept_summary(concept)
            for concept in corefs_index.knowledge_concept_projections()[:safe_limit]
        ]
    }


@router.get("/concepts/{concept_id}")
async def get_concept(
    request: Request,
    concept_id: int,
    userId: int,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    await require_unlocked_user_async(request, userId)
    corefs_index = _active_knowledge_index(userId)
    concept = corefs_index.knowledge_concept_projection(concept_id)
    if concept is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Concept not found.",
        )
    return _corefs_concept_response(concept)


@router.post("/sources/{source_id}/compile", status_code=status.HTTP_202_ACCEPTED)
async def compile_source(
    request: Request,
    source_id: int,
    userId: int,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    session = await require_unlocked_user_async(request, userId)
    projection = next(
        (
            item
            for item in canonical_knowledge_view(session=session).knowledge_source_projections()
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
        "evidenceSpans": [_corefs_evidence_response(projection) for projection in projections],
    }


@router.get("/export")
async def export_knowledge(
    request: Request,
    userId: int,
    runtime_db: Session = Depends(get_runtime_db),
) -> Response:
    session = await require_unlocked_user_async(request, userId)
    with tempfile.TemporaryDirectory(prefix="anima-okf-export-") as temp_dir:
        bundle_dir = Path(temp_dir) / "bundle"
        concepts = canonical_knowledge_view(session=session).knowledge_concept_projections()
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
        try:
            concepts = read_portable_okf_bundle(bundle_dir=bundle_dir)
            for concept in concepts:
                publish_canonical_knowledge_source(
                    session=session,
                    stable_id=stable_opaque_id(
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


@router.post("/lint")
async def lint_knowledge(
    request: Request,
    payload: KnowledgeLintRequest,
    runtime_db: Session = Depends(get_runtime_db),
) -> dict[str, Any]:
    session = await require_unlocked_user_async(request, payload.userId)
    return {
        "findings": _canonical_lint_findings(
            session=session,
            source_id=payload.sourceId,
            concept_id=payload.conceptId,
        )
    }


def _active_knowledge_index(user_id: int) -> Any:
    from anima_server.services.corefs.asset_authority import (
        require_canonical_asset_session,
    )

    session = require_canonical_asset_session(user_id)
    return canonical_knowledge_view(session=session)


def _canonical_text_document(
    payload: TextSourceRequest,
    *,
    kind: str,
) -> CanonicalKnowledgeDocument:
    content = payload.content.strip()
    default_name = "knowledge.md" if kind == "markdown" else "knowledge.txt"
    filename = _canonical_source_filename(payload.filename, default=default_name)
    normalized = parse_markdown_structure(content).to_markdown() if kind == "markdown" else content
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
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or any(character.isspace() for character in normalized)
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
