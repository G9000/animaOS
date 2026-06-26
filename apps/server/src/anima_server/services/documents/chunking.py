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
    overlap_chars: int = 0,
) -> list[ExtractedDocumentChunk]:
    target = max(1, target_chars)
    if overlap_chars != 0:
        raise ValueError("overlap_chars must be 0 until chunk overlap is supported.")

    chunks: list[ExtractedDocumentChunk] = []
    current_parts: list[_ChunkPart] = []
    current_length = 0

    def flush() -> None:
        nonlocal current_length
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
        current_parts.clear()
        current_length = 0

    for part in _iter_chunk_parts(pages, target):
        next_length = _combined_length(current_length, part.text)
        if current_parts and next_length > target:
            flush()

        if not current_parts and len(part.text) > target:
            current_parts.append(part)
            current_length = len(part.text)
            flush()
            continue

        current_parts.append(part)
        current_length = _combined_length(current_length, part.text)

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


def _combined_length(current_length: int, text: str) -> int:
    if current_length == 0:
        return len(text)
    return current_length + 2 + len(text)


__all__ = ["chunk_pages"]
