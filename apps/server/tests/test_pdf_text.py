from __future__ import annotations

from pathlib import Path

import pytest
from anima_server.services.agent import text_processing
from anima_server.services.agent.text_processing import prepare_memory_text
from anima_server.services.documents.pdf_text import (
    PageText,
    extract_pdf_text,
    normalize_pdf_page_text,
)
from pdf_fixtures import write_text_pdf


def test_normalize_pdf_page_text_uses_memory_text_pdf_spacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(text_processing.anima_core_bindings, "rust_fix_pdf_spacing", None)
    raw_text = "s pa cing\tstays\r\n\r\nclean\u0000"

    normalized = normalize_pdf_page_text(raw_text)

    assert normalized == prepare_memory_text(raw_text, apply_pdf_spacing=True)
    assert normalized == "spacing stays clean"


def test_page_text_records_page_number_and_text() -> None:
    assert PageText(page_number=3, text="body") == PageText(page_number=3, text="body")


def test_extract_pdf_text_reads_page_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "manual.pdf"
    write_text_pdf(pdf_path, "Hello PDF extraction")

    pages = extract_pdf_text(str(pdf_path))

    assert pages == [PageText(page_number=1, text="Hello PDF extraction")]


def test_extract_pdf_text_raises_controlled_read_error(tmp_path: Path) -> None:
    pdf_path = tmp_path / "not-a-pdf.pdf"
    pdf_path.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match=r"Failed to read PDF file",
    ):
        extract_pdf_text(str(pdf_path))
