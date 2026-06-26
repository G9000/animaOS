from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from anima_server.services.agent.text_processing import prepare_memory_text


@dataclass(frozen=True, slots=True)
class PageText:
    page_number: int
    text: str


def normalize_pdf_page_text(text: str) -> str:
    return prepare_memory_text(text, apply_pdf_spacing=True)


def extract_pdf_text(path: str) -> list[PageText]:
    pdf_path = Path(path)
    try:
        reader = PdfReader(str(pdf_path))
    except (OSError, PdfReadError, ValueError) as exc:
        raise RuntimeError(f"Failed to read PDF file {pdf_path.name}: {exc}") from exc

    if reader.is_encrypted:
        try:
            decrypt_result = reader.decrypt("")
        except Exception as exc:
            raise RuntimeError(
                f"PDF is encrypted and could not be decrypted: {pdf_path.name}"
            ) from exc
        if decrypt_result == 0:
            raise RuntimeError(f"PDF is encrypted and requires a password: {pdf_path.name}")

    pages: list[PageText] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text() or ""
        except Exception as exc:
            raise RuntimeError(
                f"Failed to extract text from page {page_number} of {pdf_path.name}: {exc}"
            ) from exc

        text = normalize_pdf_page_text(raw_text)
        if text:
            pages.append(PageText(page_number=page_number, text=text))

    if not pages:
        raise RuntimeError(f"PDF contains no extractable text: {pdf_path.name}")
    return pages


__all__ = ["PageText", "extract_pdf_text", "normalize_pdf_page_text"]
