from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class DocumentByteSource(Protocol):
    name: str
    size: int

    def read_all(self, *, max_bytes: int) -> bytes: ...


type DocumentInput = str | DocumentByteSource


@dataclass(frozen=True, slots=True)
class DocumentRegistration:
    user_id: int
    filename: str
    mime_type: str
    storage_path: str
    sha256: str
    size_bytes: int
    thread_id: int | None = None
    workflow_run_id: int | None = None
    metadata_json: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ExtractedDocumentChunk:
    chunk_index: int
    content_text: str
    page_start: int | None = None
    page_end: int | None = None
    section_title: str | None = None
    token_count: int | None = None
    metadata_json: dict[str, object] | None = None
