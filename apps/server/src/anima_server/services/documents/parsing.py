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
    pack_status,
    parsing_pack_ready,
)
from anima_server.services.documents.pdf_text import PageText, extract_pdf_text

logger = logging.getLogger(__name__)

_DOCLING_PAGE_BREAK = "\f"

PARSE_QUALITY_PREVIEW = "preview"
PARSE_QUALITY_DOCLING = "docling"


class DocumentParsingError(RuntimeError):
    """Raised when no parser can extract text from a document."""


class DocumentAwaitingParserError(DocumentParsingError):
    """Raised when a scanned/image-only document needs the quality parser.

    The pdfium preview path has no OCR, so it cannot extract text from a
    scanned PDF. When the Docling parsing pack (which does OCR) is not yet
    ready, that is a transient condition, not a permanent failure — the
    caller should let the ingest retry once the pack finishes downloading,
    rather than treating this as an unrecoverable error.
    """


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
    try:
        pages = extract_pdf_text(path)
    except RuntimeError as exc:
        if "no extractable text" in str(exc) and not parsing_pack_ready():
            status = pack_status()
            if status.state == "absent":
                # The docling extra isn't installed, so ensure_parsing_pack()
                # (called above) can never make progress — nothing will ever
                # arrive. Telling the caller to "wait" would be a permanent
                # lie; tell them what to actually do instead.
                raise DocumentParsingError(
                    f"{exc} The document appears to be scanned (no text layer) "
                    "and the quality parser with OCR is not installed — install "
                    "the server's docling extra (or download the parsing pack) "
                    "to ingest scanned PDFs."
                ) from exc
            if status.state == "error":
                # The download already failed; retrying the same ingest will
                # hit this same dead end forever unless the pack download is
                # retried explicitly.
                raise DocumentParsingError(
                    f"{exc} The document appears to be scanned (no text layer); "
                    f"the parsing pack download failed ({status.error}) — retry "
                    "it via POST /documents/parsing-pack/download."
                ) from exc
            # state == "downloading" (the common case), or "ready" in the rare
            # race where the pack finished between our parsing_pack_ready()
            # check above and this one — either way it's safe to treat as
            # transient: a "ready" race just means the caller retries once
            # more than strictly necessary before the next ingest picks up
            # docling, whereas any other verdict here would be unsafe.
            raise DocumentAwaitingParserError(
                f"{exc} The document appears to be scanned (no text layer); "
                "the quality parser with OCR is not ready yet — wait for the "
                "parsing pack download to finish, then resume or re-upload."
            ) from exc
        raise
    return ExtractionOutcome(pages=pages, parse_quality=PARSE_QUALITY_PREVIEW)


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
    "DocumentAwaitingParserError",
    "DocumentParsingError",
    "ExtractionOutcome",
    "extract_document_text",
]
