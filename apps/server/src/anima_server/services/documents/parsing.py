"""Docling-first document parsing.

Docling (layout analysis + table structure + OCR) is the only durable
parser; there are no quality tiers and no escalation heuristics. When the
parsing pack is not ready yet, extraction falls back to the pdfium preview
path and the result is marked ``parse_quality="preview"`` so reparse can
upgrade it later. Docling markdown headings feed the structured chunker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from anima_server.services.documents.parsing_pack import (
    ensure_parsing_pack,
    parsing_pack_ready,
)
from anima_server.services.documents.pdf_text import PageText, extract_pdf_text

logger = logging.getLogger(__name__)

_DOCLING_PAGE_BREAK = "\f"

PARSE_QUALITY_PREVIEW = "preview"
PARSE_QUALITY_DOCLING = "docling"


class DocumentParsingError(RuntimeError):
    """Raised when no parser can extract text from a document."""


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    pages: list[PageText]
    parse_quality: str


def extract_document_text(path: str) -> ExtractionOutcome:
    if parsing_pack_ready():
        try:
            return ExtractionOutcome(
                pages=_docling_pages(path), parse_quality=PARSE_QUALITY_DOCLING
            )
        except DocumentParsingError:
            raise
        except Exception:
            # Spec §Error handling: a Docling crash must not fail the ingest —
            # fall back to preview quality (visible via parse_quality) so the
            # document stays usable and reparse can retry later.
            logger.warning("Docling parse failed for %s; using preview", path, exc_info=True)
    else:
        ensure_parsing_pack()
        logger.info("Parsing pack not ready; extracting preview text for %s", path)
    return ExtractionOutcome(
        pages=extract_pdf_text(path), parse_quality=PARSE_QUALITY_PREVIEW
    )


def _docling_pages(path: str) -> list[PageText]:
    markdown = _convert_with_docling(path)
    pages = [
        PageText(page_number=index, text=page_text.strip())
        for index, page_text in enumerate(markdown.split(_DOCLING_PAGE_BREAK), start=1)
    ]
    non_empty = [page for page in pages if page.text]
    if not non_empty:
        raise DocumentParsingError(
            f"Docling could not extract any text from {path}."
        )
    return non_empty


def _convert_with_docling(path: str) -> str:
    """The only Docling-touching function; imports lazily, OCR enabled.

    Models load per call and are released afterwards so the quality tier has
    no resident memory cost between ingestions.
    """
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:  # pragma: no cover - guarded by parsing_pack_ready
        raise DocumentParsingError(
            "The docling extra is not installed; cannot run the quality parser."
        ) from exc

    pipeline_options = PdfPipelineOptions(do_ocr=True)
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )
    result = converter.convert(path)
    return result.document.export_to_markdown(
        page_break_placeholder=_DOCLING_PAGE_BREAK,
        traverse_pictures=True,
    )


__all__ = [
    "PARSE_QUALITY_DOCLING",
    "PARSE_QUALITY_PREVIEW",
    "DocumentParsingError",
    "ExtractionOutcome",
    "extract_document_text",
]
