"""Structured document intermediate for source ingestion.

Every parsed format normalizes to one shape before chunking: an ordered list
of blocks (headings, paragraphs, tables, code) carrying locators, from which
heading-hierarchy sections and size-targeted chunks are derived. Downstream
layers (spans, embeddings, tools, compiler) stay format-agnostic.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from anima_server.services.documents.pdf_text import PageText

SECTION_PATH_SEPARATOR = " > "

# ~256-512 token target approximated as chars/4.
DEFAULT_CHUNK_TARGET_CHARS = 1600
DEFAULT_CHUNK_MIN_CHARS = 200
DEFAULT_CHUNK_OVERLAP_CHARS = 150

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^(```|~~~)")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+(\S.*)$")


@dataclass(frozen=True, slots=True)
class StructuredBlock:
    kind: str  # "heading" | "paragraph" | "table" | "code"
    text: str
    heading_level: int | None = None
    page_number: int | None = None
    line_start: int | None = None
    line_end: int | None = None

    @property
    def is_atomic(self) -> bool:
        return self.kind in {"table", "code"}


@dataclass(frozen=True, slots=True)
class StructuredSection:
    """A heading plus its direct content, up to the next heading of any level."""

    index: int
    path: tuple[str, ...]
    heading_level: int
    blocks: tuple[StructuredBlock, ...]

    @property
    def section_path(self) -> str:
        return SECTION_PATH_SEPARATOR.join(self.path)

    @property
    def content_text(self) -> str:
        """Body text only — the heading lives in `path`/`section_path`."""
        return "\n\n".join(
            block.text
            for block in self.blocks
            if block.kind != "heading" and block.text.strip()
        )

    @property
    def line_start(self) -> int | None:
        starts = [b.line_start for b in self.blocks if b.line_start is not None]
        return min(starts) if starts else None

    @property
    def line_end(self) -> int | None:
        ends = [b.line_end for b in self.blocks if b.line_end is not None]
        return max(ends) if ends else None

    @property
    def page_start(self) -> int | None:
        pages = [b.page_number for b in self.blocks if b.page_number is not None]
        return min(pages) if pages else None

    @property
    def page_end(self) -> int | None:
        pages = [b.page_number for b in self.blocks if b.page_number is not None]
        return max(pages) if pages else None


@dataclass(frozen=True, slots=True)
class SectionChunk:
    """A retrieval-sized chunk derived from one or more adjacent sections."""

    chunk_index: int
    content_text: str
    section_path: str
    section_indexes: tuple[int, ...]
    part: int = 1
    is_atomic: bool = False
    page_start: int | None = None
    page_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True, slots=True)
class StructuredDocument:
    blocks: tuple[StructuredBlock, ...] = field(default_factory=tuple)

    def sections(self) -> list[StructuredSection]:
        """Group blocks into heading-path sections.

        A section is one heading plus the content before the next heading of
        any level; its path is the chain of ancestor headings. Content before
        the first heading forms an untitled level-0 section.
        """
        sections: list[StructuredSection] = []
        path_stack: list[tuple[int, str]] = []
        current_blocks: list[StructuredBlock] = []
        current_path: tuple[str, ...] = ()
        current_level = 0

        def flush() -> None:
            nonlocal current_blocks
            has_content = any(
                block.kind != "heading" and block.text.strip() for block in current_blocks
            )
            if has_content or (current_blocks and current_path):
                sections.append(
                    StructuredSection(
                        index=len(sections),
                        path=current_path,
                        heading_level=current_level,
                        blocks=tuple(current_blocks),
                    )
                )
            current_blocks = []

        for block in self.blocks:
            if block.kind == "heading" and block.heading_level is not None:
                flush()
                level = block.heading_level
                while path_stack and path_stack[-1][0] >= level:
                    path_stack.pop()
                path_stack.append((level, block.text))
                current_path = tuple(title for _lvl, title in path_stack)
                current_level = level
                current_blocks = [block]
            else:
                current_blocks.append(block)
        flush()
        return sections

    def outline(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for section in self.sections():
            entry: dict[str, object] = {
                "section_index": section.index,
                "section_path": section.section_path,
                "heading_level": section.heading_level,
            }
            if section.line_start is not None:
                entry["line_start"] = section.line_start
                entry["line_end"] = section.line_end
            if section.page_start is not None:
                entry["page_start"] = section.page_start
                entry["page_end"] = section.page_end
            entries.append(entry)
        return entries

    def to_markdown(self) -> str:
        rendered: list[str] = []
        for block in self.blocks:
            if block.kind == "heading" and block.heading_level is not None:
                rendered.append(f"{'#' * block.heading_level} {block.text}")
            else:
                rendered.append(block.text)
        return "\n\n".join(part for part in rendered if part.strip())


def parse_markdown_structure(content: str) -> StructuredDocument:
    """Parse markdown into blocks: headings, paragraphs, fenced code, pipe tables."""
    blocks: list[StructuredBlock] = []
    lines = content.splitlines()
    index = 0
    total = len(lines)

    def paragraph_flush(buffer: list[tuple[int, str]]) -> None:
        if not buffer:
            return
        text = "\n".join(line for _no, line in buffer).strip()
        if text:
            blocks.append(
                StructuredBlock(
                    kind="paragraph",
                    text=text,
                    line_start=buffer[0][0],
                    line_end=buffer[-1][0],
                )
            )
        buffer.clear()

    paragraph_buffer: list[tuple[int, str]] = []
    while index < total:
        line = lines[index]
        line_no = index + 1

        fence = _FENCE_RE.match(line.strip())
        if fence:
            paragraph_flush(paragraph_buffer)
            marker = fence.group(1)
            code_lines = [line]
            index += 1
            while index < total:
                code_lines.append(lines[index])
                if lines[index].strip().startswith(marker):
                    index += 1
                    break
                index += 1
            blocks.append(
                StructuredBlock(
                    kind="code",
                    text="\n".join(code_lines),
                    line_start=line_no,
                    line_end=line_no + len(code_lines) - 1,
                )
            )
            continue

        heading = _MD_HEADING_RE.match(line)
        if heading:
            paragraph_flush(paragraph_buffer)
            blocks.append(
                StructuredBlock(
                    kind="heading",
                    text=heading.group(2).strip(),
                    heading_level=len(heading.group(1)),
                    line_start=line_no,
                    line_end=line_no,
                )
            )
            index += 1
            continue

        if _TABLE_ROW_RE.match(line):
            paragraph_flush(paragraph_buffer)
            table_lines = []
            while index < total and _TABLE_ROW_RE.match(lines[index]):
                table_lines.append(lines[index])
                index += 1
            blocks.append(
                StructuredBlock(
                    kind="table",
                    text="\n".join(table_lines),
                    line_start=line_no,
                    line_end=line_no + len(table_lines) - 1,
                )
            )
            continue

        if not line.strip():
            paragraph_flush(paragraph_buffer)
            index += 1
            continue

        paragraph_buffer.append((line_no, line))
        index += 1

    paragraph_flush(paragraph_buffer)
    return StructuredDocument(blocks=tuple(blocks))


def parse_page_structure(pages: Sequence[PageText]) -> StructuredDocument:
    """Best-effort structure for extracted page text (PDF fast path).

    Heading detection is deliberately conservative: single-line paragraphs that
    are numbered ("3.2 Results") or short ALL-CAPS lines. Everything else stays
    a paragraph carrying its page locator.
    """
    blocks: list[StructuredBlock] = []
    for page in pages:
        for paragraph in _split_page_paragraphs(page.text):
            heading = _detect_page_heading(paragraph)
            if heading is not None:
                text, level = heading
                blocks.append(
                    StructuredBlock(
                        kind="heading",
                        text=text,
                        heading_level=level,
                        page_number=page.page_number,
                    )
                )
            else:
                blocks.append(
                    StructuredBlock(
                        kind="paragraph",
                        text=paragraph,
                        page_number=page.page_number,
                    )
                )
    return StructuredDocument(blocks=tuple(blocks))


def structure_pages_markdown(pages: Sequence[PageText]) -> StructuredDocument:
    """Structure page text that may contain markdown (Docling quality tier).

    Each page is parsed as markdown — explicit headings, tables, and code
    survive — and plain single-line paragraphs still get the conservative
    page-heading detection, so fast-path pypdf text works through the same
    function. Blocks carry page locators instead of line locators.
    """
    blocks: list[StructuredBlock] = []
    for page in pages:
        for block in parse_markdown_structure(page.text).blocks:
            if block.kind == "paragraph":
                heading = _detect_page_heading(block.text)
                if heading is not None:
                    text, level = heading
                    blocks.append(
                        StructuredBlock(
                            kind="heading",
                            text=text,
                            heading_level=level,
                            page_number=page.page_number,
                        )
                    )
                    continue
            blocks.append(
                StructuredBlock(
                    kind=block.kind,
                    text=block.text,
                    heading_level=block.heading_level,
                    page_number=page.page_number,
                )
            )
    return StructuredDocument(blocks=tuple(blocks))


_BLANK_LINE_RE = re.compile(r"\n\s*\n+")


def _split_page_paragraphs(text: str) -> list[str]:
    return [part.strip() for part in _BLANK_LINE_RE.split(text) if part.strip()]


def _detect_page_heading(paragraph: str) -> tuple[str, int] | None:
    if "\n" in paragraph or len(paragraph) > 80:
        return None
    numbered = _NUMBERED_HEADING_RE.match(paragraph)
    if numbered and len(numbered.group(2)) <= 70 and not paragraph.endswith("."):
        level = paragraph.count(".", 0, len(numbered.group(1))) + 1
        return paragraph, min(level, 6)
    letters = [char for char in paragraph if char.isalpha()]
    if len(letters) >= 3 and all(char.isupper() for char in letters):
        return paragraph, 1
    return None


def chunk_structured_document(
    document: StructuredDocument,
    *,
    target_chars: int = DEFAULT_CHUNK_TARGET_CHARS,
    min_chars: int = DEFAULT_CHUNK_MIN_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> list[SectionChunk]:
    """Structure-aware chunking: sections merged up to target, split when over.

    - Small adjacent sections merge until the target size.
    - Oversized sections split at paragraph boundaries into parts; overlap is
      carried between parts of the same section only.
    - Table and code blocks are atomic: never split, emitted alone when they
      exceed the target.
    """
    target = max(1, target_chars)
    minimum = max(0, min(min_chars, target))
    chunks: list[SectionChunk] = []
    pending: list[StructuredSection] = []
    pending_length = 0

    def flush_pending() -> None:
        nonlocal pending, pending_length
        if not pending:
            return
        content = "\n\n".join(
            text
            for text in (_section_chunk_text(section) for section in pending)
            if text
        )
        if content:
            chunks.append(
                _section_chunk(
                    chunk_index=len(chunks),
                    content_text=content,
                    sections=pending,
                )
            )
        pending = []
        pending_length = 0

    for section in document.sections():
        # Size on the text that will actually be emitted so heading-only
        # sections (whose body is empty but whose heading is kept) still
        # count toward the target and trigger flushes.
        section_length = len(_section_chunk_text(section))
        # A heading-only section has no splittable body — the oversized path
        # (which skips heading blocks) would drop it. Only split sections
        # that actually have body content.
        if section_length > target and section.content_text:
            flush_pending()
            for part_chunk in _split_oversized_section(
                section,
                target=target,
                overlap_chars=overlap_chars,
                next_chunk_index=len(chunks),
            ):
                chunks.append(part_chunk)
            continue

        if pending and pending_length + section_length > target and pending_length >= minimum:
            flush_pending()
        pending.append(section)
        pending_length += section_length

    flush_pending()
    return chunks


@dataclass(slots=True)
class _SectionPart:
    texts: list[str] = field(default_factory=list)
    length: int = 0
    page_start: int | None = None
    page_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    is_atomic: bool = False

    @property
    def content_text(self) -> str:
        return "\n\n".join(self.texts)

    def add(self, text: str, block: StructuredBlock) -> None:
        self.texts.append(text)
        self.length += len(text)
        if block.page_number is not None:
            self.page_start = (
                block.page_number
                if self.page_start is None
                else min(self.page_start, block.page_number)
            )
            self.page_end = (
                block.page_number
                if self.page_end is None
                else max(self.page_end, block.page_number)
            )
        if block.line_start is not None:
            self.line_start = (
                block.line_start
                if self.line_start is None
                else min(self.line_start, block.line_start)
            )
        if block.line_end is not None:
            self.line_end = (
                block.line_end
                if self.line_end is None
                else max(self.line_end, block.line_end)
            )


def _split_oversized_section(
    section: StructuredSection,
    *,
    target: int,
    overlap_chars: int,
    next_chunk_index: int,
) -> Iterable[SectionChunk]:
    parts: list[_SectionPart] = []
    current = _SectionPart()

    def flush_part() -> None:
        nonlocal current
        if current.texts:
            parts.append(current)
        current = _SectionPart()

    for block in section.blocks:
        if block.kind == "heading" or not block.text.strip():
            continue
        if block.is_atomic:
            flush_part()
            atomic = _SectionPart(is_atomic=True)
            atomic.add(block.text, block)
            parts.append(atomic)
            continue
        pieces = (
            _split_long_text(block.text, target)
            if len(block.text) > target
            else [block.text]
        )
        for piece in pieces:
            if current.texts and current.length + len(piece) > target:
                flush_part()
            current.add(piece, block)
    flush_part()

    chunk_parts: list[SectionChunk] = []
    previous_tail = ""
    for offset, part in enumerate(parts):
        content = (
            f"{previous_tail}\n\n{part.content_text}"
            if previous_tail
            else part.content_text
        )
        chunk_parts.append(
            SectionChunk(
                chunk_index=next_chunk_index + offset,
                content_text=content,
                section_path=section.section_path,
                section_indexes=(section.index,),
                part=offset + 1,
                is_atomic=part.is_atomic,
                page_start=part.page_start,
                page_end=part.page_end,
                line_start=part.line_start,
                line_end=part.line_end,
            )
        )
        previous_tail = (
            "" if part.is_atomic else _overlap_tail(part.content_text, overlap_chars)
        )
    return chunk_parts


def _section_chunk_text(section: StructuredSection) -> str:
    """A section's chunkable text; heading-only sections keep their heading.

    Body text normally excludes headings (they live in the section path),
    but a section that is *only* a heading — e.g. an ALL-CAPS warning line
    the page-heading detector classified — has no body at all, and dropping
    it would erase that text from the index entirely.
    """
    content = section.content_text
    if content:
        return content
    return section.path[-1] if section.path else ""


def _split_long_text(text: str, target: int) -> list[str]:
    words = text.split()
    pieces: list[str] = []
    current: list[str] = []
    current_length = 0
    for word in words:
        next_length = len(word) if not current else current_length + 1 + len(word)
        if current and next_length > target:
            pieces.append(" ".join(current))
            current = []
            current_length = 0
        current.append(word)
        current_length = len(word) if current_length == 0 else current_length + 1 + len(word)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _overlap_tail(text: str, overlap_chars: int) -> str:
    if overlap_chars <= 0 or len(text) <= overlap_chars:
        return ""
    tail = text[-overlap_chars:]
    boundary = tail.find(" ")
    if boundary != -1:
        tail = tail[boundary + 1 :]
    return tail.strip()


def _section_chunk(
    *,
    chunk_index: int,
    content_text: str,
    sections: Sequence[StructuredSection],
    part: int = 1,
    is_atomic: bool = False,
) -> SectionChunk:
    primary = sections[0]
    line_starts = [s.line_start for s in sections if s.line_start is not None]
    line_ends = [s.line_end for s in sections if s.line_end is not None]
    page_starts = [s.page_start for s in sections if s.page_start is not None]
    page_ends = [s.page_end for s in sections if s.page_end is not None]
    return SectionChunk(
        chunk_index=chunk_index,
        content_text=content_text,
        section_path=primary.section_path,
        section_indexes=tuple(section.index for section in sections),
        part=part,
        is_atomic=is_atomic,
        page_start=min(page_starts) if page_starts else None,
        page_end=max(page_ends) if page_ends else None,
        line_start=min(line_starts) if line_starts else None,
        line_end=max(line_ends) if line_ends else None,
    )


__all__ = [
    "DEFAULT_CHUNK_MIN_CHARS",
    "DEFAULT_CHUNK_OVERLAP_CHARS",
    "DEFAULT_CHUNK_TARGET_CHARS",
    "SECTION_PATH_SEPARATOR",
    "SectionChunk",
    "StructuredBlock",
    "StructuredDocument",
    "StructuredSection",
    "chunk_structured_document",
    "parse_markdown_structure",
    "parse_page_structure",
    "structure_pages_markdown",
]
