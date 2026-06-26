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


def _write_text_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT\n/F1 12 Tf\n72 120 Td\n({escaped}) Tj\nET\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"endstream",
    ]

    chunks = [b"%PDF-1.4\n"]
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n")

    xref_offset = sum(len(chunk) for chunk in chunks)
    xref = [f"xref\n0 {len(objects) + 1}\n".encode("ascii")]
    xref.append(b"0000000000 65535 f \n")
    for offset in offsets:
        xref.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    xref.append(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )

    path.write_bytes(b"".join(chunks + xref))


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
    _write_text_pdf(pdf_path, "Hello PDF extraction")

    pages = extract_pdf_text(str(pdf_path))

    assert pages == [PageText(page_number=1, text="Hello PDF extraction")]


def test_extract_pdf_text_raises_controlled_read_error(tmp_path: Path) -> None:
    pdf_path = tmp_path / "not-a-pdf.pdf"
    pdf_path.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(
        RuntimeError,
        match=r"Failed to read PDF file not-a-pdf\.pdf",
    ):
        extract_pdf_text(str(pdf_path))
