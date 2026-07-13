"""Tiered PDF text extraction: pypdf fast path, optional Docling quality tier.

The fast path (pypdf) is the default for born-digital PDFs. When the
``docling`` extra is installed, poor fast-path output — scanned pages, near-
empty extractions — escalates to Docling, which runs layout analysis plus OCR
and exports per-page markdown. Both tiers return the same ``PageText`` shape,
so workflow checkpoints and chunking stay tier-agnostic; the quality tier's
markdown headings are picked up downstream by the structured chunker.
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from anima_server.config import settings
from anima_server.services.documents.pdf_text import PageText, extract_pdf_text

logger = logging.getLogger(__name__)

PARSER_TIER_FAST = "fast"
PARSER_TIER_QUALITY = "quality"
PARSER_TIER_AUTO = "auto"

_DOCLING_PAGE_BREAK = "\f"

# Escalation heuristics (auto tier): a page this sparse suggests a scan or a
# layout pypdf could not read; escalate when at least this share of pages is
# sparse.
_SPARSE_PAGE_WORD_COUNT = 15
_SPARSE_PAGE_SHARE_THRESHOLD = 0.5


class DocumentParsingError(RuntimeError):
    """Raised when no parsing tier can extract text from a document."""


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    pages: list[PageText]
    tier: str


def docling_available() -> bool:
    return importlib.util.find_spec("docling") is not None


def extract_document_text(path: str) -> list[PageText]:
    """ExtractTextFn-compatible entry point for the PDF workflow."""
    return extract_document_text_with_tier(path).pages


def extract_document_text_with_tier(path: str) -> ExtractionOutcome:
    tier = (settings.document_parser_tier or PARSER_TIER_AUTO).strip().lower()
    if tier not in {PARSER_TIER_FAST, PARSER_TIER_QUALITY, PARSER_TIER_AUTO}:
        raise ValueError(f"Unknown document_parser_tier: {tier!r}")

    if tier == PARSER_TIER_QUALITY:
        if docling_available():
            return ExtractionOutcome(pages=_docling_pages(path), tier=PARSER_TIER_QUALITY)
        logger.warning(
            "document_parser_tier=quality but the docling extra is not "
            "installed; falling back to the fast parser."
        )
        return ExtractionOutcome(pages=extract_pdf_text(path), tier=PARSER_TIER_FAST)

    try:
        pages = extract_pdf_text(path)
    except RuntimeError as exc:
        if tier == PARSER_TIER_AUTO and _is_no_text_error(exc) and docling_available():
            logger.info("Fast PDF parse found no text; escalating to Docling OCR.")
            return ExtractionOutcome(pages=_docling_pages(path), tier=PARSER_TIER_QUALITY)
        if _is_no_text_error(exc) and not docling_available():
            raise DocumentParsingError(
                f"{exc} The document may be scanned; install the server's "
                "'docling' extra to enable layout parsing and OCR."
            ) from exc
        raise

    if (
        tier == PARSER_TIER_AUTO
        and should_escalate_extraction(pages)
        and docling_available()
    ):
        logger.info(
            "Fast PDF parse looks sparse (%d pages); escalating to Docling.",
            len(pages),
        )
        return ExtractionOutcome(pages=_docling_pages(path), tier=PARSER_TIER_QUALITY)
    return ExtractionOutcome(pages=pages, tier=PARSER_TIER_FAST)


def should_escalate_extraction(pages: Sequence[PageText]) -> bool:
    """Deterministic quality score: escalate when most pages are near-empty."""
    if not pages:
        return True
    sparse = sum(
        1 for page in pages if len(page.text.split()) < _SPARSE_PAGE_WORD_COUNT
    )
    return (sparse / len(pages)) >= _SPARSE_PAGE_SHARE_THRESHOLD


def _is_no_text_error(exc: RuntimeError) -> bool:
    return "no extractable text" in str(exc)


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
    except ImportError as exc:  # pragma: no cover - guarded by docling_available
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
    "PARSER_TIER_AUTO",
    "PARSER_TIER_FAST",
    "PARSER_TIER_QUALITY",
    "DocumentParsingError",
    "ExtractionOutcome",
    "docling_available",
    "extract_document_text",
    "extract_document_text_with_tier",
    "should_escalate_extraction",
]
