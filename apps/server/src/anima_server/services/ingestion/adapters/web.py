from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeSource, RuntimeSourceArtifact, RuntimeSourceSpan
from anima_server.services.ingestion.adapters.text import (
    STRUCTURED_MARKDOWN_ARTIFACT_KIND,
    _content_hash,
    _paragraph_spans,
    _sanitize_filename,
    _structured_markdown_spans,
)
from anima_server.services.ingestion.artifacts import replace_source_artifacts_and_spans
from anima_server.services.ingestion.html_extract import (
    HTML_EXTRACTOR_NAME,
    HtmlExtraction,
    extract_html_article,
)
from anima_server.services.ingestion.models import (
    SourceArtifactInput,
    SourceIdentity,
    SourceSpanInput,
)
from anima_server.services.ingestion.retrieval import EmbeddingFn
from anima_server.services.ingestion.sources import register_source
from anima_server.services.ingestion.structured import parse_markdown_structure

RAW_HTML_ARTIFACT_KIND = "raw_html"

IngestResult = tuple[RuntimeSource, list[RuntimeSourceArtifact], list[RuntimeSourceSpan]]


def ingest_web_capture(
    db: Session,
    *,
    user_id: int,
    url: str,
    readable_text: str | None = None,
    html: str | None = None,
    title: str | None = None,
    canonical_url: str | None = None,
    embedding_fn: EmbeddingFn | None = None,
    compile_knowledge: bool = True,
) -> IngestResult:
    """Ingest a captured web page.

    Exactly one of *readable_text* (pre-extracted text, the legacy mode) or
    *html* (raw captured HTML, extracted server-side) must be provided.
    """
    if (readable_text is None) == (html is None):
        raise ValueError("provide exactly one of readable_text or html")
    source_url = _normalize_url(url)
    canonical = _normalize_url(canonical_url) if canonical_url else None

    if html is not None:
        normalized_html = html.strip()
        if not normalized_html:
            raise ValueError("content must not be empty")
        return _ingest_html(
            db,
            user_id=user_id,
            kind="web_capture",
            source_uri=source_url,
            html=normalized_html,
            title=title,
            canonical_url=canonical,
            embedding_fn=embedding_fn,
            compile_knowledge=compile_knowledge,
        )

    normalized = (readable_text or "").strip()
    if not normalized:
        raise ValueError("content must not be empty")

    metadata = {
        "url": source_url,
        "canonical_url": canonical,
    }
    source = register_source(
        db,
        SourceIdentity(
            user_id=user_id,
            kind="web_capture",
            source_uri=source_url,
            content_hash=_content_hash(f"{source_url}\n{normalized}"),
            title=title,
            media_type="text/plain",
            metadata_json=metadata,
        ),
    )
    artifacts = [
        SourceArtifactInput(
            artifact_kind="readable_text",
            content_text=normalized,
            content_hash=_content_hash(normalized),
            metadata_json=metadata,
        )
    ]
    spans = _paragraph_spans("readable_text", normalized)
    return (
        source,
        *replace_source_artifacts_and_spans(
            db,
            source=source,
            artifacts=artifacts,
            spans=spans,
            embedding_fn=embedding_fn,
            compile_knowledge=compile_knowledge,
        ),
    )


def ingest_html_content(
    db: Session,
    *,
    user_id: int,
    html: str,
    filename: str | None = None,
    title: str | None = None,
    embedding_fn: EmbeddingFn | None = None,
    compile_knowledge: bool = True,
) -> IngestResult:
    """Ingest an uploaded ``.html`` file through the extraction pipeline."""
    normalized_html = html.strip()
    if not normalized_html:
        raise ValueError("content must not be empty")
    safe_name = _sanitize_filename(filename, default="page.html")
    return _ingest_html(
        db,
        user_id=user_id,
        kind="html",
        source_uri=f"html://{safe_name}",
        html=normalized_html,
        title=title,
        canonical_url=None,
        embedding_fn=embedding_fn,
        compile_knowledge=compile_knowledge,
        extra_metadata={"filename": safe_name},
    )


def reextract_source_html(
    db: Session,
    *,
    user_id: int,
    source_id: int,
    embedding_fn: EmbeddingFn | None = None,
    compile_knowledge: bool = False,
) -> IngestResult:
    """Re-run extraction on a source's stored raw HTML artifact.

    Replaces artifacts and spans idempotently (unchanged extraction output
    keeps existing span rows). Raises LookupError when the source does not
    exist and ValueError when it has no stored raw HTML.
    """
    source = db.scalar(
        select(RuntimeSource).where(
            RuntimeSource.id == source_id,
            RuntimeSource.user_id == user_id,
        )
    )
    if source is None:
        raise LookupError("Source not found.")
    raw_artifact = db.scalar(
        select(RuntimeSourceArtifact)
        .where(
            RuntimeSourceArtifact.source_id == source.id,
            RuntimeSourceArtifact.artifact_kind == RAW_HTML_ARTIFACT_KIND,
        )
        .order_by(RuntimeSourceArtifact.id.desc())
    )
    html = (raw_artifact.content_text or "").strip() if raw_artifact else ""
    if not html:
        raise ValueError("Source has no stored raw HTML artifact.")

    url = source.source_uri if source.kind == "web_capture" else None
    extraction = extract_html_article(html, url=url)
    base_metadata = dict(source.metadata_json or {})
    metadata = _extraction_metadata(base_metadata, extraction)
    artifacts, spans = _html_artifacts_and_spans(
        html,
        extraction=extraction,
        metadata=metadata,
    )
    return (
        source,
        *replace_source_artifacts_and_spans(
            db,
            source=source,
            artifacts=artifacts,
            spans=spans,
            embedding_fn=embedding_fn,
            compile_knowledge=compile_knowledge,
        ),
    )


def _ingest_html(
    db: Session,
    *,
    user_id: int,
    kind: str,
    source_uri: str,
    html: str,
    title: str | None,
    canonical_url: str | None,
    embedding_fn: EmbeddingFn | None,
    compile_knowledge: bool,
    extra_metadata: dict[str, object] | None = None,
) -> IngestResult:
    url = source_uri if kind == "web_capture" else None
    extraction = extract_html_article(html, url=url)

    base_metadata: dict[str, object] = dict(extra_metadata or {})
    if kind == "web_capture":
        base_metadata["url"] = source_uri
        base_metadata["canonical_url"] = canonical_url or extraction.canonical_url
    metadata = _extraction_metadata(base_metadata, extraction)

    artifacts, spans = _html_artifacts_and_spans(
        html,
        extraction=extraction,
        metadata=metadata,
    )
    source = register_source(
        db,
        SourceIdentity(
            user_id=user_id,
            kind=kind,
            source_uri=source_uri,
            content_hash=_content_hash(f"{source_uri}\n{html}"),
            title=title or extraction.title,
            media_type="text/html",
            metadata_json=metadata,
        ),
    )
    return (
        source,
        *replace_source_artifacts_and_spans(
            db,
            source=source,
            artifacts=artifacts,
            spans=spans,
            embedding_fn=embedding_fn,
            compile_knowledge=compile_knowledge,
        ),
    )


def _html_artifacts_and_spans(
    html: str,
    *,
    extraction: HtmlExtraction,
    metadata: dict[str, object],
) -> tuple[list[SourceArtifactInput], list[SourceSpanInput]]:
    """Raw HTML artifact (kept for re-extraction) + structured markdown spans."""
    document = parse_markdown_structure(extraction.markdown)
    canonical_markdown = document.to_markdown()
    artifacts = [
        SourceArtifactInput(
            artifact_kind=RAW_HTML_ARTIFACT_KIND,
            content_text=html,
            content_hash=_content_hash(html),
            metadata_json=metadata,
        ),
        SourceArtifactInput(
            artifact_kind=STRUCTURED_MARKDOWN_ARTIFACT_KIND,
            content_text=canonical_markdown,
            content_hash=_content_hash(canonical_markdown),
            metadata_json={**metadata, "outline": document.outline()},
        ),
    ]
    spans = _structured_markdown_spans(STRUCTURED_MARKDOWN_ARTIFACT_KIND, document)
    return artifacts, spans


def _extraction_metadata(
    base_metadata: dict[str, object],
    extraction: HtmlExtraction,
) -> dict[str, object]:
    metadata: dict[str, object] = dict(base_metadata)
    metadata.pop("outline", None)
    metadata["extractor"] = HTML_EXTRACTOR_NAME
    for key, value in (
        ("author", extraction.author),
        ("published_date", extraction.date),
        ("sitename", extraction.sitename),
    ):
        if value:
            metadata[key] = value
    return metadata


def _normalize_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(
        char.isspace() for char in normalized
    ):
        raise ValueError("url must be an absolute http(s) URL")
    return normalized


__all__ = [
    "RAW_HTML_ARTIFACT_KIND",
    "ingest_html_content",
    "ingest_web_capture",
    "reextract_source_html",
]
