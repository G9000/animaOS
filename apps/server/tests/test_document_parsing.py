from __future__ import annotations

from pathlib import Path

import pytest
from anima_server.services.documents import parsing
from anima_server.services.documents.pdf_text import PageText
from pdf_fixtures import write_text_pdf


def test_uses_docling_when_pack_ready(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(parsing, "parsing_pack_ready", lambda: True)
    monkeypatch.setattr(
        parsing, "_docling_pages", lambda path: [PageText(page_number=1, text="# Title\n\nBody")]
    )

    outcome = parsing.extract_document_text(str(tmp_path / "doc.pdf"))

    assert outcome.parse_quality == "docling"
    assert outcome.pages[0].text.startswith("# Title")


def test_falls_back_to_preview_and_triggers_pack_download(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(parsing, "parsing_pack_ready", lambda: False)
    ensured: list[bool] = []
    monkeypatch.setattr(parsing, "ensure_parsing_pack", lambda: ensured.append(True))
    pdf_path = tmp_path / "doc.pdf"
    write_text_pdf(pdf_path, "preview body text")

    outcome = parsing.extract_document_text(str(pdf_path))

    assert outcome.parse_quality == "preview"
    assert outcome.pages == [PageText(page_number=1, text="preview body text")]
    assert ensured == [True]


def test_docling_crash_falls_back_to_preview(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(parsing, "parsing_pack_ready", lambda: True)

    def boom(path: str) -> list[PageText]:
        raise RuntimeError("docling exploded")

    monkeypatch.setattr(parsing, "_docling_pages", boom)
    pdf_path = tmp_path / "doc.pdf"
    write_text_pdf(pdf_path, "fallback body")

    outcome = parsing.extract_document_text(str(pdf_path))

    assert outcome.parse_quality == "preview"
    assert outcome.pages == [PageText(page_number=1, text="fallback body")]


def test_docling_producing_nothing_raises_parsing_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(parsing, "parsing_pack_ready", lambda: True)
    monkeypatch.setattr(parsing, "_convert_with_docling", lambda path: "")

    with pytest.raises(parsing.DocumentParsingError):
        parsing.extract_document_text(str(tmp_path / "doc.pdf"))


def test_scanned_pdf_while_pack_not_ready_raises_awaiting_parser(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(parsing, "parsing_pack_ready", lambda: False)
    ensured: list[bool] = []
    monkeypatch.setattr(parsing, "ensure_parsing_pack", lambda: ensured.append(True))
    pdf_path = tmp_path / "scanned.pdf"
    write_text_pdf(pdf_path, "")

    with pytest.raises(parsing.DocumentAwaitingParserError) as exc_info:
        parsing.extract_document_text(str(pdf_path))

    assert ensured == [True]
    assert "parsing pack" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_scanned_pdf_while_pack_ready_is_plain_runtime_error(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(parsing, "parsing_pack_ready", lambda: True)

    def boom(path: str) -> list[PageText]:
        raise RuntimeError("docling exploded")

    monkeypatch.setattr(parsing, "_docling_pages", boom)
    pdf_path = tmp_path / "scanned.pdf"
    write_text_pdf(pdf_path, "")

    with pytest.raises(RuntimeError) as exc_info:
        parsing.extract_document_text(str(pdf_path))

    assert not isinstance(exc_info.value, parsing.DocumentAwaitingParserError)
    assert "no extractable text" in str(exc_info.value)
