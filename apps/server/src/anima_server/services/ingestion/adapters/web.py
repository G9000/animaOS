from __future__ import annotations

from urllib.parse import urlparse

from anima_server.services.ingestion.adapters.text import (
    STRUCTURED_MARKDOWN_ARTIFACT_KIND,
    _content_hash,
    _structured_markdown_spans,
)
from anima_server.services.ingestion.html_extract import (
    HTML_EXTRACTOR_NAME,
    HtmlExtraction,
)
from anima_server.services.ingestion.models import (
    SourceArtifactInput,
    SourceSpanInput,
)
from anima_server.services.ingestion.structured import parse_markdown_structure

RAW_HTML_ARTIFACT_KIND = "raw_html"


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
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or any(char.isspace() for char in normalized)
    ):
        raise ValueError("url must be an absolute http(s) URL")
    return normalized


__all__ = [
    "RAW_HTML_ARTIFACT_KIND",
]
