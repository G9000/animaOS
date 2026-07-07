from __future__ import annotations

import json
import re
from collections.abc import Sequence

from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeSource, RuntimeSourceSpan
from anima_server.services.ingestion.compiler import CompileResult, compile_source_to_concepts
from anima_server.services.ingestion.retrieval import EmbeddingFn


def compile_source_knowledge(
    db: Session,
    *,
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
    embedding_fn: EmbeddingFn | None = None,
) -> CompileResult:
    return compile_source_to_concepts(
        db,
        user_id=source.user_id,
        source_id=source.id,
        span_ids=[span.id for span in spans],
        model=lambda _request: json.dumps(_source_payload(source, spans)),
        embedding_fn=embedding_fn,
    )


def _source_payload(
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
) -> dict[str, object]:
    title = _source_title(source)
    kind_tag = _slugify(source.kind)
    summary_slug = f"source-{source.id}-{_slugify(title)}"
    concepts: list[dict[str, object]] = [
        {
            "type": "source_summary",
            "slug": summary_slug,
            "title": title,
            "description": f"Compiled source summary for {title}.",
            "body_markdown": _summary_body(source, spans),
            "source_span_ids": [span.id for span in spans],
            "tags": ["compiled", "source", kind_tag, "source_summary"],
        }
    ]
    links: list[dict[str, object]] = []
    for span in spans:
        span_title = _span_title(source, span)
        slug = f"source-{source.id}-span-{_span_index(span)}"
        concepts.append(
            {
                "type": "topic",
                "slug": slug,
                "title": span_title,
                "description": f"Source-backed section from {title}.",
                "body_markdown": _span_body(source, span, span_title),
                "source_span_ids": [span.id],
                "tags": ["compiled", "source", kind_tag, "source_span"],
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
            "compiler": "runtime_source",
            "runtime_source_id": source.id,
            "source_kind": source.kind,
        },
    }


def _summary_body(
    source: RuntimeSource,
    spans: Sequence[RuntimeSourceSpan],
) -> str:
    title = _source_title(source)
    lines = [
        f"# {title}",
        "",
        f"Compiled {source.kind} knowledge source. Each section keeps citations to original source spans.",
        "",
    ]
    for span in spans:
        span_title = _span_title(source, span)
        location = _span_location(span)
        preview = _compact_text(span.content_text, limit=280)
        lines.append(f"## {span_title}{location}")
        lines.append(preview)
        lines.append("")
    return "\n".join(lines).strip()


def _span_body(
    source: RuntimeSource,
    span: RuntimeSourceSpan,
    title: str,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"Source: {_source_title(source)}{_span_location(span)}.",
    ]
    lines.extend(["", span.content_text.strip()])
    return "\n".join(lines).strip()


def _span_title(source: RuntimeSource, span: RuntimeSourceSpan) -> str:
    metadata = span.metadata_json or {}
    for key in ("section_title", "heading", "annotation_kind"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    span_index = _span_index(span)
    span_kind = span.span_kind.replace("_", " ").title()
    return f"{_source_title(source)} {span_kind} {span_index + 1}"


def _source_title(source: RuntimeSource) -> str:
    return source.title or source.source_uri or f"Source {source.id}"


def _span_index(span: RuntimeSourceSpan) -> int:
    locator = span.locator_json or {}
    for key in ("chunk_index", "paragraph_index", "line_start", "row_start"):
        raw_index = locator.get(key)
        if isinstance(raw_index, int):
            return raw_index
    return span.id


def _span_location(span: RuntimeSourceSpan) -> str:
    locator = span.locator_json or {}
    page_start = locator.get("page_start")
    page_end = locator.get("page_end")
    if isinstance(page_start, int):
        if not isinstance(page_end, int) or page_end == page_start:
            return f" (page {page_start})"
        return f" (pages {page_start}-{page_end})"
    line_start = locator.get("line_start")
    line_end = locator.get("line_end")
    if isinstance(line_start, int):
        if not isinstance(line_end, int) or line_end == line_start:
            return f" (line {line_start})"
        return f" (lines {line_start}-{line_end})"
    row_start = locator.get("row_start")
    row_end = locator.get("row_end")
    if isinstance(row_start, int):
        if not isinstance(row_end, int) or row_end == row_start:
            return f" (row {row_start})"
        return f" (rows {row_start}-{row_end})"
    time_start = locator.get("time_start_ms")
    time_end = locator.get("time_end_ms")
    if isinstance(time_start, int):
        if not isinstance(time_end, int) or time_end == time_start:
            return f" ({time_start} ms)"
        return f" ({time_start}-{time_end} ms)"
    cell = locator.get("cell")
    if isinstance(cell, str) and cell.strip():
        return f" (cell {cell.strip()})"
    return ""


def _compact_text(value: str, *, limit: int) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "document"
