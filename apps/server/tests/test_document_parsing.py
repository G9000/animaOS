from __future__ import annotations

from typing import Any

import pytest
from anima_server.services.documents import parsing
from anima_server.services.documents.chunking import chunk_pages_structured
from anima_server.services.documents.pdf_text import PageText

_DENSE_PAGE = PageText(
    page_number=1,
    text=" ".join(["word"] * 120),
)
_SPARSE_PAGE = PageText(page_number=2, text="only three words")


def _set_tier(monkeypatch: Any, tier: str) -> None:
    monkeypatch.setattr(parsing.settings, "document_parser_tier", tier)


def test_should_escalate_when_most_pages_sparse() -> None:
    assert parsing.should_escalate_extraction([_SPARSE_PAGE, _SPARSE_PAGE])
    assert parsing.should_escalate_extraction([])
    assert not parsing.should_escalate_extraction([_DENSE_PAGE, _DENSE_PAGE])
    # Exactly half sparse escalates (>= threshold).
    assert parsing.should_escalate_extraction([_DENSE_PAGE, _SPARSE_PAGE])


def test_fast_tier_never_escalates(monkeypatch: Any) -> None:
    _set_tier(monkeypatch, "fast")
    monkeypatch.setattr(parsing, "extract_pdf_text", lambda path: [_SPARSE_PAGE])
    monkeypatch.setattr(parsing, "docling_available", lambda: True)
    monkeypatch.setattr(
        parsing,
        "_convert_with_docling",
        lambda path: pytest.fail("docling must not run in fast tier"),
    )

    outcome = parsing.extract_document_text_with_tier("doc.pdf")

    assert outcome.tier == "fast"
    assert outcome.pages == [_SPARSE_PAGE]


def test_auto_tier_keeps_fast_result_when_dense(monkeypatch: Any) -> None:
    _set_tier(monkeypatch, "auto")
    monkeypatch.setattr(parsing, "extract_pdf_text", lambda path: [_DENSE_PAGE])
    monkeypatch.setattr(parsing, "docling_available", lambda: True)
    monkeypatch.setattr(
        parsing,
        "_convert_with_docling",
        lambda path: pytest.fail("docling must not run for dense extraction"),
    )

    outcome = parsing.extract_document_text_with_tier("doc.pdf")

    assert outcome.tier == "fast"
    assert outcome.pages == [_DENSE_PAGE]


def test_auto_tier_escalates_sparse_extraction_to_docling(monkeypatch: Any) -> None:
    _set_tier(monkeypatch, "auto")
    monkeypatch.setattr(parsing, "extract_pdf_text", lambda path: [_SPARSE_PAGE])
    monkeypatch.setattr(parsing, "docling_available", lambda: True)
    monkeypatch.setattr(
        parsing,
        "_convert_with_docling",
        lambda path: "# Section One\n\nRecovered body text.\f## Section Two\n\nMore text.",
    )

    outcome = parsing.extract_document_text_with_tier("doc.pdf")

    assert outcome.tier == "quality"
    assert [page.page_number for page in outcome.pages] == [1, 2]
    assert outcome.pages[0].text.startswith("# Section One")


def test_auto_tier_escalates_scanned_pdf_to_docling(monkeypatch: Any) -> None:
    _set_tier(monkeypatch, "auto")

    def raise_no_text(path: str) -> list[PageText]:
        raise RuntimeError(f"PDF contains no extractable text: {path}")

    monkeypatch.setattr(parsing, "extract_pdf_text", raise_no_text)
    monkeypatch.setattr(parsing, "docling_available", lambda: True)
    monkeypatch.setattr(
        parsing,
        "_convert_with_docling",
        lambda path: "OCR recovered this scanned page.",
    )

    outcome = parsing.extract_document_text_with_tier("scan.pdf")

    assert outcome.tier == "quality"
    assert outcome.pages[0].text == "OCR recovered this scanned page."


def test_scanned_pdf_without_docling_reports_actionable_error(monkeypatch: Any) -> None:
    _set_tier(monkeypatch, "auto")

    def raise_no_text(path: str) -> list[PageText]:
        raise RuntimeError(f"PDF contains no extractable text: {path}")

    monkeypatch.setattr(parsing, "extract_pdf_text", raise_no_text)
    monkeypatch.setattr(parsing, "docling_available", lambda: False)

    with pytest.raises(parsing.DocumentParsingError, match="docling"):
        parsing.extract_document_text_with_tier("scan.pdf")


def test_other_fast_errors_pass_through_unchanged(monkeypatch: Any) -> None:
    _set_tier(monkeypatch, "auto")

    def raise_encrypted(path: str) -> list[PageText]:
        raise RuntimeError("PDF is encrypted and requires a password: locked.pdf")

    monkeypatch.setattr(parsing, "extract_pdf_text", raise_encrypted)
    monkeypatch.setattr(parsing, "docling_available", lambda: True)

    with pytest.raises(RuntimeError, match="encrypted"):
        parsing.extract_document_text_with_tier("locked.pdf")


def test_quality_tier_uses_docling_directly(monkeypatch: Any) -> None:
    _set_tier(monkeypatch, "quality")
    monkeypatch.setattr(parsing, "docling_available", lambda: True)
    monkeypatch.setattr(
        parsing,
        "extract_pdf_text",
        lambda path: pytest.fail("fast parser must not run in quality tier"),
    )
    monkeypatch.setattr(parsing, "_convert_with_docling", lambda path: "Quality text.")

    outcome = parsing.extract_document_text_with_tier("doc.pdf")

    assert outcome.tier == "quality"
    assert outcome.pages == [PageText(page_number=1, text="Quality text.")]


def test_quality_tier_falls_back_to_fast_when_docling_missing(monkeypatch: Any) -> None:
    _set_tier(monkeypatch, "quality")
    monkeypatch.setattr(parsing, "docling_available", lambda: False)
    monkeypatch.setattr(parsing, "extract_pdf_text", lambda path: [_DENSE_PAGE])

    outcome = parsing.extract_document_text_with_tier("doc.pdf")

    assert outcome.tier == "fast"
    assert outcome.pages == [_DENSE_PAGE]


def test_unknown_tier_raises(monkeypatch: Any) -> None:
    _set_tier(monkeypatch, "turbo")

    with pytest.raises(ValueError, match="document_parser_tier"):
        parsing.extract_document_text_with_tier("doc.pdf")


def test_docling_empty_output_raises(monkeypatch: Any) -> None:
    _set_tier(monkeypatch, "quality")
    monkeypatch.setattr(parsing, "docling_available", lambda: True)
    monkeypatch.setattr(parsing, "_convert_with_docling", lambda path: " \f \f ")

    with pytest.raises(parsing.DocumentParsingError, match="could not extract"):
        parsing.extract_document_text_with_tier("doc.pdf")


def test_structured_chunking_preserves_docling_sections() -> None:
    install_body = " ".join(["Mount the relay before wiring the pump."] * 8)
    calibrate_body = " ".join(["Calibrate at 40 PSI before sealing."] * 8)
    pages = [
        PageText(page_number=1, text=f"# Installation\n\n{install_body}"),
        PageText(
            page_number=2,
            text=(
                f"## Calibration\n\n{calibrate_body}\n\n"
                "| knob | value |\n| - | - |\n| A | 40 |"
            ),
        ),
    ]

    chunks = chunk_pages_structured(pages, target_chars=200)

    titles = [chunk.section_title for chunk in chunks]
    assert "Installation" in titles[0]
    assert any(
        title is not None and "Calibration" in title for title in titles
    )
    assert all(chunk.page_start is not None for chunk in chunks)
    table_chunks = [
        chunk for chunk in chunks if "| knob |" in chunk.content_text
    ]
    assert len(table_chunks) == 1


def test_plain_pypdf_pages_get_conservative_heading_detection() -> None:
    pages = [
        PageText(
            page_number=1,
            text="2.1 Safety Procedures\n\nAlways disconnect power before servicing.",
        ),
    ]

    chunks = chunk_pages_structured(pages, target_chars=500)

    assert chunks[0].section_title == "2.1 Safety Procedures"
    assert chunks[0].page_start == 1
