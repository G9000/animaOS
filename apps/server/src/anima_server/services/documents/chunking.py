from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from anima_server.services.documents.models import ExtractedDocumentChunk
from anima_server.services.documents.pdf_text import PageText

_BLANK_LINE_RE = re.compile(r"\n\s*\n+")


@dataclass(frozen=True, slots=True)
class _ChunkPart:
    text: str
    page_number: int


def chunk_pages(
    pages: Sequence[PageText],
    *,
    target_chars: int = 1800,
    overlap_chars: int = 200,
) -> list[ExtractedDocumentChunk]:
    target = max(1, target_chars)
    if overlap_chars < 0:
        raise ValueError("overlap_chars must be >= 0.")
    # Overlap beyond half the target would let carried text dominate new chunks.
    overlap = min(overlap_chars, target // 2)

    chunks: list[ExtractedDocumentChunk] = []
    current_parts: list[_ChunkPart] = []
    current_length = 0
    has_new_content = False
    pending_overlap: _ChunkPart | None = None

    def flush() -> None:
        nonlocal current_length, has_new_content, pending_overlap
        if not current_parts:
            return

        content_text = "\n\n".join(part.text for part in current_parts)
        chunks.append(
            ExtractedDocumentChunk(
                chunk_index=len(chunks),
                content_text=content_text,
                page_start=current_parts[0].page_number,
                page_end=current_parts[-1].page_number,
            )
        )
        if overlap > 0:
            tail = _overlap_tail(content_text, overlap)
            pending_overlap = (
                _ChunkPart(text=tail, page_number=current_parts[-1].page_number)
                if tail
                else None
            )
        current_parts.clear()
        current_length = 0
        has_new_content = False

    def seed_overlap() -> None:
        nonlocal current_length, pending_overlap
        if pending_overlap is None or current_parts:
            return
        current_parts.append(pending_overlap)
        current_length = len(pending_overlap.text)
        pending_overlap = None

    for part in _iter_chunk_parts(pages, target):
        seed_overlap()
        next_length = _combined_length(current_length, part.text)
        if has_new_content and next_length > target:
            flush()
            seed_overlap()

        if not has_new_content and len(part.text) > target:
            current_parts.append(part)
            has_new_content = True
            flush()
            continue

        current_parts.append(part)
        current_length = _combined_length(current_length, part.text)
        has_new_content = True

    flush()
    return chunks


def _iter_chunk_parts(
    pages: Sequence[PageText],
    target_chars: int,
) -> Iterable[_ChunkPart]:
    for page in pages:
        for paragraph in _split_paragraphs(page.text):
            if len(paragraph) <= target_chars:
                yield _ChunkPart(text=paragraph, page_number=page.page_number)
                continue

            for segment in _split_oversized_paragraph(paragraph, target_chars):
                yield _ChunkPart(text=segment, page_number=page.page_number)


def _split_paragraphs(text: str) -> list[str]:
    return [paragraph.strip() for paragraph in _BLANK_LINE_RE.split(text) if paragraph.strip()]


def _split_oversized_paragraph(paragraph: str, target_chars: int) -> Iterable[str]:
    words = paragraph.split()
    if not words:
        return

    current_words: list[str] = []
    current_length = 0
    for word in words:
        next_length = len(word) if not current_words else current_length + 1 + len(word)
        if current_words and next_length > target_chars:
            yield " ".join(current_words)
            current_words = []
            current_length = 0

        if len(word) > target_chars:
            yield word
            continue

        current_words.append(word)
        current_length = len(word) if current_length == 0 else current_length + 1 + len(word)

    if current_words:
        yield " ".join(current_words)


def chunk_pages_structured(
    pages: Sequence[PageText],
    *,
    target_chars: int = 1800,
    overlap_chars: int = 200,
) -> list[ExtractedDocumentChunk]:
    """Structure-aware chunking for the PDF workflow.

    Pages (plain pypdf text or Docling markdown) are structured into heading
    sections, then chunked along section boundaries; `section_title` records
    the heading path. Overlap applies within oversized sections only —
    section boundaries are semantic boundaries.
    """
    from anima_server.services.documents.store import _SECTION_TITLE_MAX_LENGTH
    from anima_server.services.ingestion.structured import (
        chunk_structured_document,
        structure_pages_markdown,
    )

    document = structure_pages_markdown(pages)
    path_by_section_index = {
        section.index: section.section_path for section in document.sections()
    }
    chunks: list[ExtractedDocumentChunk] = []
    for chunk in chunk_structured_document(
        document,
        target_chars=target_chars,
        overlap_chars=overlap_chars,
    ):
        # Small adjacent sections merge into one chunk; keep every merged
        # section path addressable (the outline/read tools match by path).
        section_paths = [
            path
            for path in (
                path_by_section_index.get(index, "")
                for index in chunk.section_indexes
            )
            if path
        ]
        metadata: dict[str, object] | None = None
        # Also needed when the chunk's own path is empty (untitled preamble
        # merged with a titled section) or longer than the section_title
        # column (which truncates at insert) so the full path stays
        # addressable by name and searchable.
        if section_paths and (
            len(section_paths) > 1
            or not chunk.section_path
            or len(chunk.section_path) > _SECTION_TITLE_MAX_LENGTH
        ):
            metadata = {"section_paths": section_paths}
        chunks.append(
            ExtractedDocumentChunk(
                chunk_index=chunk.chunk_index,
                content_text=chunk.content_text,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                section_title=chunk.section_path or None,
                metadata_json=metadata,
            )
        )
    return chunks


def _combined_length(current_length: int, text: str) -> int:
    if current_length == 0:
        return len(text)
    return current_length + 2 + len(text)


def _overlap_tail(text: str, overlap_chars: int) -> str:
    if len(text) <= overlap_chars:
        return ""
    tail = text[-overlap_chars:]
    boundary = tail.find(" ")
    if boundary != -1:
        tail = tail[boundary + 1 :]
    return tail.strip()


__all__ = ["chunk_pages", "chunk_pages_structured"]
