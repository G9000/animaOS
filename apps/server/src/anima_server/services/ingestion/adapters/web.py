from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeSource, RuntimeSourceArtifact, RuntimeSourceSpan
from anima_server.services.ingestion.adapters.text import _content_hash, _paragraph_spans
from anima_server.services.ingestion.artifacts import replace_source_artifacts_and_spans
from anima_server.services.ingestion.models import SourceArtifactInput, SourceIdentity
from anima_server.services.ingestion.sources import register_source


def ingest_web_capture(
    db: Session,
    *,
    user_id: int,
    url: str,
    readable_text: str,
    title: str | None = None,
    canonical_url: str | None = None,
) -> tuple[RuntimeSource, list[RuntimeSourceArtifact], list[RuntimeSourceSpan]]:
    source_url = _normalize_url(url)
    canonical = _normalize_url(canonical_url) if canonical_url else None
    normalized = readable_text.strip()
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
    return (source, *replace_source_artifacts_and_spans(db, source=source, artifacts=artifacts, spans=spans))


def _normalize_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(
        char.isspace() for char in normalized
    ):
        raise ValueError("url must be an absolute http(s) URL")
    return normalized
