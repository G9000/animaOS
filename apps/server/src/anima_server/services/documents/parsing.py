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
    ParsingPackStatus,
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
    prior_status: ParsingPackStatus | None = None
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
        # Snapshot status BEFORE triggering the retry below. ensure_parsing_pack()
        # clears any recorded download error and starts a fresh attempt, so if we
        # queried status only *after* calling it, a persistent failure would look
        # identical to "still downloading" and its actionable error message (see
        # commit debe594) would become permanently unreachable. We still call
        # ensure_parsing_pack() every time — auto-retry is desired — we just must
        # not let it erase the evidence of the failure it's about to retry.
        prior_status = pack_status()
        ensure_parsing_pack()
        logger.info("Parsing pack not ready; extracting preview text for %s", path)
    try:
        pages = extract_pdf_text(path)
    except RuntimeError as exc:
        if "no extractable text" in str(exc) and not parsing_pack_ready():
            # Use the pre-retry snapshot, not a fresh pack_status() call: by now
            # ensure_parsing_pack() has run and may have cleared an "error" state
            # into "downloading", which would hide the failure below.
            status = prior_status if prior_status is not None else pack_status()
            if status.state == "error":
                # The download already failed; ensure_parsing_pack() above has
                # just kicked off a new attempt (auto-retry), but the caller
                # still needs to know the previous one failed rather than
                # silently waiting on a retry it doesn't know is happening.
                raise DocumentParsingError(
                    f"{exc} The document appears to be scanned (no text layer); "
                    f"the parsing pack download previously failed ({status.error}) "
                    "— a new download attempt has been started automatically; "
                    "check GET /documents/parsing-pack for progress, or retry it "
                    "explicitly via POST /documents/parsing-pack/download."
                ) from exc
            if status.state == "absent":
                # "absent" alone is ambiguous: it means either "docling isn't
                # installed" or "docling is installed but no download has ever
                # been attempted" — both read the same before ensure runs.
                # Disambiguate by checking status again now that
                # ensure_parsing_pack() has had a chance to act: it only leaves
                # the pack "absent" when docling isn't installed (it no-ops in
                # that case), so still-absent here means truly not installed —
                # telling the caller to "wait" would be a permanent lie, so tell
                # them what to actually do instead. Any other post-ensure state
                # means this was genuinely the first download attempt, just
                # kicked off above, so we fall through to the awaiting-parser
                # branch below.
                post_ensure_status = pack_status()
                if post_ensure_status.state == "absent":
                    raise DocumentParsingError(
                        f"{exc} The document appears to be scanned (no text layer) "
                        "and the quality parser with OCR is not installed — install "
                        "the server's docling extra (or download the parsing pack) "
                        "to ingest scanned PDFs."
                    ) from exc
            # state == "downloading" (the common case), the first-download-just-
            # started case falling through from "absent" above, or "ready" in the
            # rare race where the pack finished between our parsing_pack_ready()
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
