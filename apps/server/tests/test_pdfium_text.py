from __future__ import annotations

from pathlib import Path

import pytest
from anima_server.services.documents.pdf_text import PageText
from anima_server.services.documents.pdfium_text import extract_pdf_text_pdfium
from pdf_fixtures import write_text_pdf


def test_extracts_page_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "manual.pdf"
    write_text_pdf(pdf_path, "Hello pdfium extraction")

    pages = extract_pdf_text_pdfium(str(pdf_path))

    assert pages == [PageText(page_number=1, text="Hello pdfium extraction")]


def test_unreadable_file_raises_controlled_error(tmp_path: Path) -> None:
    pdf_path = tmp_path / "not-a-pdf.pdf"
    pdf_path.write_bytes(b"plain text, not a pdf")

    with pytest.raises(RuntimeError, match="Failed to read PDF file"):
        extract_pdf_text_pdfium(str(pdf_path))


def test_pdf_without_text_raises_no_extractable_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "empty.pdf"
    write_text_pdf(pdf_path, "")

    with pytest.raises(RuntimeError, match="no extractable text"):
        extract_pdf_text_pdfium(str(pdf_path))
