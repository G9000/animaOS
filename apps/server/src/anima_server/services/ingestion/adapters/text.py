from __future__ import annotations

import hashlib
import re
from pathlib import Path

from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeSource, RuntimeSourceArtifact, RuntimeSourceSpan
from anima_server.services.ingestion.artifacts import replace_source_artifacts_and_spans
from anima_server.services.ingestion.models import (
    SourceArtifactInput,
    SourceIdentity,
    SourceSpanInput,
)
from anima_server.services.ingestion.retrieval import EmbeddingFn
from anima_server.services.ingestion.sources import register_source


def ingest_text_content(
    db: Session,
    *,
    user_id: int,
    content: str,
    filename: str | None = None,
    title: str | None = None,
    embedding_fn: EmbeddingFn | None = None,
) -> tuple[RuntimeSource, list[RuntimeSourceArtifact], list[RuntimeSourceSpan]]:
    return _ingest_content(
        db,
        user_id=user_id,
        kind="text",
        artifact_kind="plain_text",
        content=content,
        filename=filename,
        title=title,
        embedding_fn=embedding_fn,
    )


def ingest_markdown_content(
    db: Session,
    *,
    user_id: int,
    content: str,
    filename: str | None = None,
    title: str | None = None,
    embedding_fn: EmbeddingFn | None = None,
) -> tuple[RuntimeSource, list[RuntimeSourceArtifact], list[RuntimeSourceSpan]]:
    return _ingest_content(
        db,
        user_id=user_id,
        kind="markdown",
        artifact_kind="markdown",
        content=content,
        filename=filename,
        title=title,
        embedding_fn=embedding_fn,
    )


def _ingest_content(
    db: Session,
    *,
    user_id: int,
    kind: str,
    artifact_kind: str,
    content: str,
    filename: str | None,
    title: str | None,
    embedding_fn: EmbeddingFn | None,
) -> tuple[RuntimeSource, list[RuntimeSourceArtifact], list[RuntimeSourceSpan]]:
    normalized = content.strip()
    if not normalized:
        raise ValueError("content must not be empty")

    safe_name = _sanitize_filename(filename, default=f"{kind}.txt")
    source = register_source(
        db,
        SourceIdentity(
            user_id=user_id,
            kind=kind,
            source_uri=f"{kind}://{safe_name}",
            content_hash=_content_hash(normalized),
            title=title or safe_name,
            media_type="text/markdown" if kind == "markdown" else "text/plain",
            metadata_json={"filename": safe_name},
        ),
    )
    artifacts = [
        SourceArtifactInput(
            artifact_kind=artifact_kind,
            content_text=normalized,
            content_hash=_content_hash(normalized),
            metadata_json={"filename": safe_name},
        )
    ]
    spans = _markdown_spans(artifact_kind, normalized) if kind == "markdown" else _paragraph_spans(
        artifact_kind,
        normalized,
    )
    return (
        source,
        *replace_source_artifacts_and_spans(
            db,
            source=source,
            artifacts=artifacts,
            spans=spans,
            embedding_fn=embedding_fn,
            compile_knowledge=True,
        ),
    )


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _markdown_spans(artifact_kind: str, content: str) -> list[SourceSpanInput]:
    spans: list[SourceSpanInput] = []
    current_heading: str | None = None
    lines = content.splitlines()
    paragraph_lines: list[tuple[int, str]] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        text = "\n".join(line for _line_no, line in paragraph_lines).strip()
        if text:
            start = paragraph_lines[0][0]
            end = paragraph_lines[-1][0]
            spans.append(
                SourceSpanInput(
                    artifact_kind=artifact_kind,
                    span_kind="paragraph",
                    locator_json={
                        "paragraph_index": len([span for span in spans if span.span_kind == "paragraph"]),
                        "line_start": start,
                        "line_end": end,
                    },
                    content_text=text,
                    content_hash=_content_hash(text),
                    metadata_json={"heading": current_heading} if current_heading else None,
                )
            )
        paragraph_lines = []

    for index, line in enumerate(lines, start=1):
        heading = _HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            text = heading.group(2).strip()
            current_heading = text
            spans.append(
                SourceSpanInput(
                    artifact_kind=artifact_kind,
                    span_kind="heading",
                    locator_json={"line_start": index, "line_end": index},
                    content_text=text,
                    content_hash=_content_hash(text),
                    metadata_json={"heading": text, "heading_level": level},
                )
            )
            continue
        if not line.strip():
            flush_paragraph()
            continue
        paragraph_lines.append((index, line))
    flush_paragraph()
    return spans


def _paragraph_spans(artifact_kind: str, content: str) -> list[SourceSpanInput]:
    spans: list[SourceSpanInput] = []
    lines = content.splitlines()
    paragraph_lines: list[tuple[int, str]] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        text = "\n".join(line for _line_no, line in paragraph_lines).strip()
        if text:
            spans.append(
                SourceSpanInput(
                    artifact_kind=artifact_kind,
                    span_kind="paragraph",
                    locator_json={
                        "paragraph_index": len(spans),
                        "line_start": paragraph_lines[0][0],
                        "line_end": paragraph_lines[-1][0],
                    },
                    content_text=text,
                    content_hash=_content_hash(text),
                )
            )
        paragraph_lines = []

    for index, line in enumerate(lines, start=1):
        if line.strip():
            paragraph_lines.append((index, line))
        else:
            flush_paragraph()
    flush_paragraph()
    return spans


def _sanitize_filename(filename: str | None, *, default: str) -> str:
    name = Path(filename or default).name.strip()
    return re.sub(r"[^a-zA-Z0-9._ -]+", "_", name)[:180] or default


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
