from __future__ import annotations

from dataclasses import dataclass

from anima_server.services.agent.text_processing import prepare_memory_text


@dataclass(frozen=True, slots=True)
class PageText:
    page_number: int
    text: str


def normalize_pdf_page_text(text: str) -> str:
    return prepare_memory_text(text, apply_pdf_spacing=True)


def extract_pdf_text(path: str) -> list[PageText]:
    from anima_server.services.documents.pdfium_text import extract_pdf_text_pdfium

    return extract_pdf_text_pdfium(path)


__all__ = ["PageText", "extract_pdf_text", "normalize_pdf_page_text"]
