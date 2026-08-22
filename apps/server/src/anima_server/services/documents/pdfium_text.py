"""Instant preview text extraction via pdfium.

pdfium (Chromium's PDF engine) has markedly better reading order and word
spacing than stream-order extractors. This module is the *preview* path only:
its output feeds immediate chat context and provisional indexing while
Docling produces the durable artifact (see parsing.py).
"""

from __future__ import annotations

from anima_server.services.documents.models import DocumentInput
from anima_server.services.documents.pdf_text import PageText, normalize_pdf_page_text


def extract_pdf_text_pdfium(source: DocumentInput) -> list[PageText]:
    import pypdfium2 as pdfium

    label = source if isinstance(source, str) else f"CoreFS object {source.name}"
    pdfium_source = (
        source
        if isinstance(source, str)
        else source.read_all(max_bytes=100 * 1024 * 1024)
    )
    try:
        document = pdfium.PdfDocument(pdfium_source)
    except Exception as exc:
        raise RuntimeError(f"Failed to read PDF file {label}: {exc}") from exc

    try:
        pages: list[PageText] = []
        for page_index in range(len(document)):
            page = document[page_index]
            textpage = page.get_textpage()
            try:
                raw_text = textpage.get_text_bounded() or ""
            finally:
                textpage.close()
                page.close()
            text = normalize_pdf_page_text(raw_text)
            if text:
                pages.append(PageText(page_number=page_index + 1, text=text))
    finally:
        document.close()

    if not pages:
        raise RuntimeError(f"PDF contains no extractable text: {label}")
    return pages


__all__ = ["extract_pdf_text_pdfium"]
