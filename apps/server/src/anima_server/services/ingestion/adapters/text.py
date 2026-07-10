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
from anima_server.services.ingestion.structured import (
    StructuredDocument,
    parse_markdown_structure,
)

STRUCTURED_MARKDOWN_ARTIFACT_KIND = "structured_markdown"


def ingest_text_content(
    db: Session,
    *,
    user_id: int,
    content: str,
    filename: str | None = None,
    title: str | None = None,
    embedding_fn: EmbeddingFn | None = None,
    compile_knowledge: bool = True,
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
        compile_knowledge=compile_knowledge,
    )


def ingest_markdown_content(
    db: Session,
    *,
    user_id: int,
    content: str,
    filename: str | None = None,
    title: str | None = None,
    embedding_fn: EmbeddingFn | None = None,
    compile_knowledge: bool = True,
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
        compile_knowledge=compile_knowledge,
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
    compile_knowledge: bool,
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
    if kind == "markdown":
        document = parse_markdown_structure(normalized)
        canonical = document.to_markdown()
        artifacts.append(
            SourceArtifactInput(
                artifact_kind=STRUCTURED_MARKDOWN_ARTIFACT_KIND,
                content_text=canonical,
                content_hash=_content_hash(canonical),
                metadata_json={"filename": safe_name, "outline": document.outline()},
            )
        )
        spans = _structured_markdown_spans(artifact_kind, document)
    else:
        spans = _paragraph_spans(artifact_kind, normalized)
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


def _structured_markdown_spans(
    artifact_kind: str,
    document: StructuredDocument,
) -> list[SourceSpanInput]:
    """Heading/paragraph evidence spans plus parent `section` spans.

    Heading and paragraph spans keep their historical shapes (kinds, locators)
    and gain `section_path`/`section_index` metadata. Section spans are the
    parent read units for structure-aware retrieval: they carry the merged
    section body, are not embedded, and are excluded from concept compilation.
    """
    spans: list[SourceSpanInput] = []
    section_spans: list[SourceSpanInput] = []
    paragraph_count = 0

    for section in document.sections():
        section_meta: dict[str, object] = {"section_index": section.index}
        if section.path:
            section_meta["section_path"] = section.section_path

        for block in section.blocks:
            if block.kind == "heading":
                spans.append(
                    SourceSpanInput(
                        artifact_kind=artifact_kind,
                        span_kind="heading",
                        locator_json={
                            "line_start": block.line_start,
                            "line_end": block.line_end,
                        },
                        content_text=block.text,
                        content_hash=_content_hash(block.text),
                        metadata_json={
                            "heading": block.text,
                            "heading_level": block.heading_level,
                            **section_meta,
                        },
                    )
                )
                continue
            if not block.text.strip():
                continue
            metadata: dict[str, object] = dict(section_meta)
            if section.path:
                metadata["heading"] = section.path[-1]
            if block.kind != "paragraph":
                metadata["block_kind"] = block.kind
            spans.append(
                SourceSpanInput(
                    artifact_kind=artifact_kind,
                    span_kind="paragraph",
                    locator_json={
                        "paragraph_index": paragraph_count,
                        "line_start": block.line_start,
                        "line_end": block.line_end,
                    },
                    content_text=block.text,
                    content_hash=_content_hash(block.text),
                    metadata_json=metadata,
                )
            )
            paragraph_count += 1

        section_content = section.content_text
        if section_content:
            section_spans.append(
                SourceSpanInput(
                    artifact_kind=STRUCTURED_MARKDOWN_ARTIFACT_KIND,
                    span_kind="section",
                    locator_json={
                        "section_index": section.index,
                        "line_start": section.line_start,
                        "line_end": section.line_end,
                    },
                    content_text=section_content,
                    content_hash=_content_hash(section_content),
                    metadata_json={
                        "section_path": section.section_path,
                        "heading_level": section.heading_level,
                        **({"heading": section.path[-1]} if section.path else {}),
                    },
                )
            )

    return [*spans, *section_spans]


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
