from __future__ import annotations

import pytest
from anima_server.services.agent import text_processing
from anima_server.services.agent.text_processing import prepare_memory_text
from anima_server.services.documents.pdf_text import (
    PageText,
    extract_pdf_text,
    normalize_pdf_page_text,
)


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


def test_extract_pdf_text_raises_controlled_parser_boundary_error() -> None:
    with pytest.raises(
        RuntimeError,
        match="requires an approved parser dependency/configuration",
    ):
        extract_pdf_text("manual.pdf")
