from __future__ import annotations

import json
import re
from collections.abc import Sequence

from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeDocument, RuntimeSource, RuntimeSourceSpan
from anima_server.services.ingestion.compiler import CompileResult, compile_source_to_concepts
from anima_server.services.ingestion.retrieval import EmbeddingFn


def compile_document_source(
    db: Session,
    *,
    document: RuntimeDocument,
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
    embedding_fn: EmbeddingFn | None = None,
) -> CompileResult:
    return compile_source_to_concepts(
        db,
        user_id=document.user_id,
        source_id=source.id,
        span_ids=[span.id for span in spans],
        model=lambda _request: json.dumps(_document_payload(document, source, spans)),
        embedding_fn=embedding_fn,
    )


def _document_payload(
    document: RuntimeDocument,
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
) -> dict[str, object]:
    summary_slug = f"document-{document.id}-{_slugify(document.filename)}"
    concepts: list[dict[str, object]] = [
        {
            "type": "source_summary",
            "slug": summary_slug,
            "title": document.filename,
            "description": f"Compiled source summary for {document.filename}.",
            "body_markdown": _summary_body(document, spans),
            "source_span_ids": [span.id for span in spans],
            "tags": ["compiled", "document", "pdf", "source_summary"],
        }
    ]
    links: list[dict[str, object]] = []
    for span in spans:
        title = _span_title(document, span)
        slug = f"document-{document.id}-chunk-{_chunk_index(span)}"
        concepts.append(
            {
                "type": "topic",
                "slug": slug,
                "title": title,
                "description": f"Source-backed document section from {document.filename}.",
                "body_markdown": _span_body(document, span, title),
                "source_span_ids": [span.id],
                "tags": ["compiled", "document", "pdf", "source_span"],
            }
        )
        links.append(
            {
                "source_slug": slug,
                "target_slug": summary_slug,
                "link_type": "supports",
                "confidence": 1.0,
            }
        )
    return {
        "concepts": concepts,
        "links": links,
        "metadata": {
            "compiler": "document_source",
            "runtime_document_id": document.id,
            "runtime_source_id": source.id,
        },
    }


def _summary_body(
    document: RuntimeDocument,
    spans: Sequence[RuntimeSourceSpan],
) -> str:
    lines = [
        f"# {document.filename}",
        "",
        "Compiled PDF knowledge source. Each section keeps citations to the original document chunks.",
        "",
    ]
    for span in spans:
        title = _span_title(document, span)
        location = _span_location(span)
        preview = _compact_text(span.content_text, limit=280)
        lines.append(f"## {title}{location}")
        lines.append(preview)
        lines.append("")
    return "\n".join(lines).strip()


def _span_body(
    document: RuntimeDocument,
    span: RuntimeSourceSpan,
    title: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"Source: {document.filename}{_span_location(span)}.",
    ]
    lines.extend(["", span.content_text.strip()])
    return "\n".join(lines).strip()


def _span_title(document: RuntimeDocument, span: RuntimeSourceSpan) -> str:
    metadata = span.metadata_json or {}
    section_title = metadata.get("section_title")
    if isinstance(section_title, str) and section_title.strip():
        return section_title.strip()
    chunk_index = _chunk_index(span)
    return f"{document.filename} Chunk {chunk_index + 1}"


def _chunk_index(span: RuntimeSourceSpan) -> int:
    locator = span.locator_json or {}
    raw_index = locator.get("chunk_index")
    if isinstance(raw_index, int):
        return raw_index
    return span.id


def _span_location(span: RuntimeSourceSpan) -> str:
    locator = span.locator_json or {}
    page_start = locator.get("page_start")
    page_end = locator.get("page_end")
    if not isinstance(page_start, int):
        return ""
    if not isinstance(page_end, int) or page_end == page_start:
        return f" (page {page_start})"
    return f" (pages {page_start}-{page_end})"


def _compact_text(value: str, *, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "document"
