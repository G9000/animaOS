# Production Document Processing — Plan 1: Parsing Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace pypdf with pdfium as the instant preview extractor, make Docling the only durable parser (no tiers, no escalation), add a parsing-pack manager with visible states, and a re-parse upgrade path — per `docs/superpowers/specs/2026-07-15-document-processing-production-design.md` §1–§3, §7 (states), Migration.

**Architecture:** `pdfium_text.py` becomes the fast extraction used only for preview; `parsing.py` is rewritten to expose exactly two extractors (preview via pdfium, durable via Docling) plus a pack-readiness check; documents and chunks carry `parse_quality` (`preview | docling | legacy`); `reparse_document()` upgrades preview/legacy documents through Docling when the pack becomes ready.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, pypdfium2 (new base dep), docling (extra), pytest.

## Global Constraints

- Worktree: `/Users/julio/animaOS/.claude/worktrees/production-doc-processing`; all commands run from `apps/server/` inside it unless noted.
- Run tests with: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest <path> -q` (from `apps/server/`).
- Never assume Ollama or any specific LLM provider (spec Goal 3).
- Docling absence must never crash ingest: documents proceed at `preview` quality in an `awaiting_parser`-visible state (spec §2).
- `parse_quality` values are exactly: `preview`, `docling`, `legacy` (spec §3).
- Do not copy code from `~/Anima` or reference it (user constraint; write fresh).
- Commit after every green task; branch `feature/pdp-010-production-doc-processing`.

---

### Task 1: pdfium preview extractor

**Files:**
- Modify: `apps/server/pyproject.toml` (add `pypdfium2>=4.30` to `[project] dependencies`)
- Create: `apps/server/src/anima_server/services/documents/pdfium_text.py`
- Create: `apps/server/tests/pdf_fixtures.py` (move `_write_text_pdf` from `tests/test_pdf_text.py` so both test modules share it)
- Test: `apps/server/tests/test_pdfium_text.py`

**Interfaces:**
- Consumes: `PageText` and `normalize_pdf_page_text` from `anima_server.services.documents.pdf_text`.
- Produces: `extract_pdf_text_pdfium(path: str) -> list[PageText]` — raises `RuntimeError` with the exact phrase `"no extractable text"` when the PDF yields nothing (Task 5's `awaiting_parser` routing and existing `_is_no_text_error` semantics rely on this phrase), and `RuntimeError` starting `"Failed to read PDF file"` on unreadable input.

- [ ] **Step 1: Create shared fixture helper**

Create `tests/pdf_fixtures.py` containing `write_text_pdf(path: Path, text: str) -> None` — copy the body of `_write_text_pdf` from `tests/test_pdf_text.py:15-51` verbatim (hand-crafted single-page PDF with a text stream). Update `tests/test_pdf_text.py` to `from tests.pdf_fixtures import write_text_pdf` and delete its local copy.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_pdfium_text.py
from __future__ import annotations

from pathlib import Path

import pytest
from anima_server.services.documents.pdf_text import PageText
from anima_server.services.documents.pdfium_text import extract_pdf_text_pdfium

from tests.pdf_fixtures import write_text_pdf


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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_pdfium_text.py -q`
Expected: FAIL — `ModuleNotFoundError: anima_server.services.documents.pdfium_text`

- [ ] **Step 4: Add dependency and implement**

In `apps/server/pyproject.toml`, add `"pypdfium2>=4.30",` to the `dependencies` list (next to `"pypdf>=6.14.2",` — pypdf is removed in Task 2). Run `uv sync --all-packages` from the repo root.

```python
# src/anima_server/services/documents/pdfium_text.py
"""Instant preview text extraction via pdfium.

pdfium (Chromium's PDF engine) has markedly better reading order and word
spacing than stream-order extractors. This module is the *preview* path only:
its output feeds immediate chat context and provisional indexing while
Docling produces the durable artifact (see parsing.py).
"""

from __future__ import annotations

from anima_server.services.documents.pdf_text import PageText, normalize_pdf_page_text


def extract_pdf_text_pdfium(path: str) -> list[PageText]:
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(path)
    except Exception as exc:
        raise RuntimeError(f"Failed to read PDF file {path}: {exc}") from exc

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
        raise RuntimeError(f"PDF contains no extractable text: {path}")
    return pages


__all__ = ["extract_pdf_text_pdfium"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_pdfium_text.py tests/test_pdf_text.py -q`
Expected: PASS (all, including the relocated-fixture module)

- [ ] **Step 6: Commit**

```bash
git add apps/server/pyproject.toml uv.lock apps/server/src/anima_server/services/documents/pdfium_text.py apps/server/tests/test_pdfium_text.py apps/server/tests/pdf_fixtures.py apps/server/tests/test_pdf_text.py
git commit -m "feat(documents): pdfium preview text extractor"
```

---

### Task 2: route pdf_text through pdfium, remove pypdf

**Files:**
- Modify: `apps/server/src/anima_server/services/documents/pdf_text.py` (replace pypdf implementation)
- Modify: `apps/server/pyproject.toml` (delete `"pypdf>=6.14.2",`)
- Test: `apps/server/tests/test_pdf_text.py` (existing tests keep passing — they assert behavior, not library)

**Interfaces:**
- Consumes: `extract_pdf_text_pdfium` from Task 1.
- Produces: `extract_pdf_text(path: str) -> list[PageText]` — same public name and error contract as today (`"Failed to read PDF file …"`, `"PDF contains no extractable text: …"`), so `parsing.py`, the workflow, and all callers are untouched.

- [ ] **Step 1: Reimplement `extract_pdf_text`**

Replace the body of `pdf_text.py` — delete the `pypdf` imports and the encrypted-PDF branch (pdfium raises on password-protected files; the generic read error covers it), keep `PageText` and `normalize_pdf_page_text` exactly as they are, and delegate:

```python
def extract_pdf_text(path: str) -> list[PageText]:
    from anima_server.services.documents.pdfium_text import extract_pdf_text_pdfium

    return extract_pdf_text_pdfium(path)
```

(`pdfium_text` imports `PageText`/`normalize_pdf_page_text` from `pdf_text`; the lazy import here avoids the cycle at module load.)

- [ ] **Step 2: Remove the dependency**

Delete `"pypdf>=6.14.2",` from `apps/server/pyproject.toml`. Run `uv sync --all-packages` from the repo root. Then verify nothing imports it:

Run: `grep -rn "from pypdf\|import pypdf" src/ tests/`
Expected: no output (docstring mentions of the word "pypdf" in `structured.py`/`chunking.py`/`config.py` are fine; update those docstrings to say "preview text" where they say "pypdf plain text").

- [ ] **Step 3: Run the affected suites**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_pdf_text.py tests/test_document_parsing.py tests/test_documents_api.py -q`
Expected: PASS. If a test in `test_pdf_text.py` asserts pypdf-specific error text (e.g. encrypted-PDF phrasing), update that test to the new generic read-error contract — the *phrases* `"Failed to read PDF file"` and `"no extractable text"` must stay.

- [ ] **Step 4: Commit**

```bash
git add -A apps/server uv.lock
git commit -m "feat(documents): pdf_text extracts via pdfium; drop pypdf"
```

---

### Task 3: parse_quality on documents and chunks

**Files:**
- Create: `apps/server/alembic_runtime/versions/027_parse_quality.py`
- Modify: `apps/server/src/anima_server/models/runtime.py` (RuntimeDocument + RuntimeDocumentChunk)
- Modify: `apps/server/src/anima_server/services/documents/store.py` (`replace_document_chunks` gains `parse_quality`)
- Test: `apps/server/tests/test_document_store.py`

**Interfaces:**
- Produces: `RuntimeDocument.parse_quality: str` and `RuntimeDocumentChunk.parse_quality: str` (values `preview|docling|legacy`, server default `legacy` so existing rows are marked for re-parse per spec Migration); `replace_document_chunks(db, *, document_id, chunks, parse_quality)` stamps both the chunks and the document.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_document_store.py
def test_replace_document_chunks_stamps_parse_quality(runtime_db, document) -> None:
    chunks = [
        ExtractedDocumentChunk(chunk_index=0, content_text="alpha", page_start=1, page_end=1)
    ]

    rows = replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=chunks,
        parse_quality="preview",
    )

    assert [row.parse_quality for row in rows] == ["preview"]
    assert document.parse_quality == "preview"
```

(Reuse this file's existing `runtime_db`/document fixtures — match the naming of neighbouring tests in the file when writing the final version.)

- [ ] **Step 2: Run test to verify it fails**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_document_store.py -q -k parse_quality`
Expected: FAIL — unexpected keyword `parse_quality` / no such column.

- [ ] **Step 3: Migration + model + store**

Migration (follow the structure of `alembic_runtime/versions/026_reembed_completions.py` for revision wiring — `down_revision` points at the current head, check with `uv run --project . alembic -c alembic_runtime.ini heads`):

```python
# alembic_runtime/versions/027_parse_quality.py
"""Add parse_quality to runtime documents and chunks."""

import sqlalchemy as sa
from alembic import op

revision = "027_parse_quality"
down_revision = "<current head — fill from alembic heads output>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runtime_documents",
        sa.Column("parse_quality", sa.String(16), nullable=False, server_default="legacy"),
    )
    op.add_column(
        "runtime_document_chunks",
        sa.Column("parse_quality", sa.String(16), nullable=False, server_default="legacy"),
    )


def downgrade() -> None:
    op.drop_column("runtime_document_chunks", "parse_quality")
    op.drop_column("runtime_documents", "parse_quality")
```

Models — add to both `RuntimeDocument` and `RuntimeDocumentChunk` in `models/runtime.py`:

```python
    parse_quality: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="legacy", default="legacy"
    )
```

Store — `replace_document_chunks` signature becomes:

```python
def replace_document_chunks(
    db: Session,
    *,
    document_id: int,
    chunks: Sequence[ExtractedDocumentChunk],
    parse_quality: str,
) -> list[RuntimeDocumentChunk]:
```

Inside, set `document.parse_quality = parse_quality` next to the existing `document.status = "registered"` line, and `parse_quality=parse_quality` on each created `RuntimeDocumentChunk`. Update every existing caller (grep `replace_document_chunks(`) to pass `parse_quality="docling"` in the workflow path for now — Task 5 threads the real value.

- [ ] **Step 4: Run tests**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_document_store.py tests/test_pdf_workflow.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A apps/server
git commit -m "feat(documents): parse_quality column on documents and chunks"
```

---

### Task 4: parsing pack manager

**Files:**
- Create: `apps/server/src/anima_server/services/documents/parsing_pack.py`
- Test: `apps/server/tests/test_parsing_pack.py`

**Interfaces:**
- Produces:
  - `pack_status() -> ParsingPackStatus` where `ParsingPackStatus` is a frozen dataclass `{state: str, progress: float | None, error: str | None}` and `state ∈ {"absent", "downloading", "ready", "error"}`.
  - `ensure_parsing_pack() -> ParsingPackStatus` — idempotent; if docling is importable but models are not yet fetched, starts a background `threading.Thread` that prefetches models and updates module state; returns immediately with the current status.
  - `parsing_pack_ready() -> bool` — convenience for Task 5's routing (`pack_status().state == "ready"`).
- Consumes: `docling` (lazily), `settings.data_dir` for the ready-marker file.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_parsing_pack.py
from __future__ import annotations

import pytest
from anima_server.services.documents import parsing_pack


@pytest.fixture(autouse=True)
def reset_pack_state(tmp_path, monkeypatch):
    monkeypatch.setattr(parsing_pack, "_marker_path", lambda: tmp_path / "parsing-pack.ready")
    parsing_pack._reset_state_for_tests()


def test_status_absent_when_docling_not_installed(monkeypatch) -> None:
    monkeypatch.setattr(parsing_pack, "_docling_installed", lambda: False)

    status = parsing_pack.pack_status()

    assert status.state == "absent"
    assert not parsing_pack.parsing_pack_ready()


def test_status_ready_when_marker_present(monkeypatch) -> None:
    monkeypatch.setattr(parsing_pack, "_docling_installed", lambda: True)
    parsing_pack._marker_path().write_text("1")

    assert parsing_pack.pack_status().state == "ready"
    assert parsing_pack.parsing_pack_ready()


def test_ensure_prefetches_models_and_marks_ready(monkeypatch) -> None:
    monkeypatch.setattr(parsing_pack, "_docling_installed", lambda: True)
    calls: list[str] = []
    monkeypatch.setattr(parsing_pack, "_prefetch_models", lambda: calls.append("fetched"))

    status = parsing_pack.ensure_parsing_pack()
    parsing_pack._wait_for_download_for_tests(timeout=5)

    assert status.state in {"downloading", "ready"}
    assert calls == ["fetched"]
    assert parsing_pack.pack_status().state == "ready"


def test_prefetch_failure_reports_error(monkeypatch) -> None:
    monkeypatch.setattr(parsing_pack, "_docling_installed", lambda: True)

    def boom() -> None:
        raise RuntimeError("network down")

    monkeypatch.setattr(parsing_pack, "_prefetch_models", boom)

    parsing_pack.ensure_parsing_pack()
    parsing_pack._wait_for_download_for_tests(timeout=5)

    status = parsing_pack.pack_status()
    assert status.state == "error"
    assert "network down" in (status.error or "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_parsing_pack.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# src/anima_server/services/documents/parsing_pack.py
"""Parsing-pack lifecycle: is the Docling quality parser present and warmed?

The pack has two parts: the docling extra (installed at build/setup time)
and its model weights (fetched once, on demand). ``ensure_parsing_pack``
prefetches weights in a background thread so ingest never blocks; documents
processed before the pack is ready stay at preview quality and are upgraded
by reparse (see reparse.py).
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from anima_server.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_download_thread: threading.Thread | None = None
_error: str | None = None


@dataclass(frozen=True, slots=True)
class ParsingPackStatus:
    state: str  # "absent" | "downloading" | "ready" | "error"
    progress: float | None = None
    error: str | None = None


def _marker_path() -> Path:
    return settings.data_dir / "parsing-pack.ready"


def _docling_installed() -> bool:
    return importlib.util.find_spec("docling") is not None


def _prefetch_models() -> None:
    """Download docling model weights by converting a trivial in-memory doc."""
    from docling.utils.model_downloader import download_models

    download_models()


def pack_status() -> ParsingPackStatus:
    if not _docling_installed():
        return ParsingPackStatus(state="absent")
    with _lock:
        if _error is not None:
            return ParsingPackStatus(state="error", error=_error)
        if _download_thread is not None and _download_thread.is_alive():
            return ParsingPackStatus(state="downloading")
    if _marker_path().exists():
        return ParsingPackStatus(state="ready")
    return ParsingPackStatus(state="absent")


def parsing_pack_ready() -> bool:
    return pack_status().state == "ready"


def ensure_parsing_pack() -> ParsingPackStatus:
    global _download_thread, _error
    if not _docling_installed():
        return ParsingPackStatus(state="absent")
    if _marker_path().exists():
        return ParsingPackStatus(state="ready")
    with _lock:
        if _download_thread is None or not _download_thread.is_alive():
            _error = None
            _download_thread = threading.Thread(
                target=_download_and_mark, name="parsing-pack-download", daemon=True
            )
            _download_thread.start()
    return pack_status()


def _download_and_mark() -> None:
    global _error
    try:
        _prefetch_models()
        _marker_path().parent.mkdir(parents=True, exist_ok=True)
        _marker_path().write_text("1")
    except Exception as exc:
        logger.warning("Parsing pack download failed", exc_info=True)
        with _lock:
            _error = str(exc)


def _reset_state_for_tests() -> None:
    global _download_thread, _error
    with _lock:
        _download_thread = None
        _error = None


def _wait_for_download_for_tests(timeout: float) -> None:
    thread = _download_thread
    if thread is not None:
        thread.join(timeout)


__all__ = ["ParsingPackStatus", "ensure_parsing_pack", "pack_status", "parsing_pack_ready"]
```

Note: `download_models` exists in docling ≥2 (`docling.utils.model_downloader`). The implementer MUST verify the import against the pinned docling version (`uv run --project . python -c "from docling.utils.model_downloader import download_models"` with the extra installed) and adjust `_prefetch_models` if the API moved — the tests monkeypatch it, so only this one function may change.

- [ ] **Step 4: Run tests**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_parsing_pack.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/server/src/anima_server/services/documents/parsing_pack.py apps/server/tests/test_parsing_pack.py
git commit -m "feat(documents): parsing pack manager (docling presence + model prefetch)"
```

---

### Task 5: Docling-always parsing (delete tiers and escalation)

**Files:**
- Modify: `apps/server/src/anima_server/services/documents/parsing.py` (rewrite)
- Modify: `apps/server/src/anima_server/config.py` (delete `document_parser_tier`)
- Modify: `apps/server/src/anima_server/services/documents/pdf_workflow.py` (extraction dependency returns pages + quality; `replace_document_chunks` gets the real quality; `text_extracted` checkpoint payload records `parse_quality`)
- Test: rewrite `apps/server/tests/test_document_parsing.py`; touch `apps/server/tests/test_pdf_workflow.py`

**Interfaces:**
- Produces (new `parsing.py` public API — everything else in the module is deleted, including `PARSER_TIER_*`, `should_escalate_extraction`, `extract_document_text_with_tier`):
  - `ExtractionOutcome` frozen dataclass: `pages: list[PageText]`, `parse_quality: str` (`"preview"` or `"docling"`).
  - `extract_document_text(path: str) -> ExtractionOutcome` — Docling when `parsing_pack_ready()`, else pdfium preview; also calls `ensure_parsing_pack()` fire-and-forget so the first ingest kicks off the model download.
  - `DocumentParsingError(RuntimeError)` — kept, raised when the chosen extractor yields nothing.
  - `_docling_pages(path) -> list[PageText]` and `_convert_with_docling(path) -> str` — keep the existing implementations verbatim (markdown export, form-feed page breaks, per-call model lifecycle).
- Consumes: `parsing_pack_ready`, `ensure_parsing_pack` (Task 4), `extract_pdf_text` (Task 2).
- Workflow contract change: `ExtractTextFn = Callable[[str], ExtractionOutcome]` in `pdf_workflow.py`; the `text_extracted` checkpoint payload gains `"parse_quality": outcome.parse_quality`; the chunked state calls `replace_document_chunks(..., parse_quality=<value from checkpoint>)`.

- [ ] **Step 1: Rewrite the parsing tests**

Replace `tests/test_document_parsing.py` wholesale. Core cases:

```python
# tests/test_document_parsing.py (new)
from __future__ import annotations

from pathlib import Path

import pytest
from anima_server.services.documents import parsing
from anima_server.services.documents.pdf_text import PageText

from tests.pdf_fixtures import write_text_pdf


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
```

- [ ] **Step 2: Run to verify failure**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_document_parsing.py -q`
Expected: FAIL — `ExtractionOutcome` has no `parse_quality` / `parsing_pack_ready` not defined.

- [ ] **Step 3: Rewrite `parsing.py`**

```python
# src/anima_server/services/documents/parsing.py (full replacement)
"""Docling-first document parsing.

Docling (layout analysis + table structure + OCR) is the only durable
parser; there are no quality tiers and no escalation heuristics. When the
parsing pack is not ready yet, extraction falls back to the pdfium preview
path and the result is marked ``parse_quality="preview"`` so reparse can
upgrade it later. Docling markdown headings feed the structured chunker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from anima_server.services.documents.parsing_pack import (
    ensure_parsing_pack,
    parsing_pack_ready,
)
from anima_server.services.documents.pdf_text import PageText, extract_pdf_text

logger = logging.getLogger(__name__)

_DOCLING_PAGE_BREAK = "\f"

PARSE_QUALITY_PREVIEW = "preview"
PARSE_QUALITY_DOCLING = "docling"


class DocumentParsingError(RuntimeError):
    """Raised when no parser can extract text from a document."""


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    pages: list[PageText]
    parse_quality: str


def extract_document_text(path: str) -> ExtractionOutcome:
    if parsing_pack_ready():
        try:
            return ExtractionOutcome(
                pages=_docling_pages(path), parse_quality=PARSE_QUALITY_DOCLING
            )
        except DocumentParsingError:
            raise
        except Exception:
            # Spec §Error handling: a Docling crash must not fail the ingest —
            # fall back to preview quality (visible via parse_quality) so the
            # document stays usable and reparse can retry later.
            logger.warning("Docling parse failed for %s; using preview", path, exc_info=True)
    else:
        ensure_parsing_pack()
        logger.info("Parsing pack not ready; extracting preview text for %s", path)
    return ExtractionOutcome(
        pages=extract_pdf_text(path), parse_quality=PARSE_QUALITY_PREVIEW
    )
```

Then append `_docling_pages` and `_convert_with_docling` **copied verbatim** from the current `parsing.py:109-148` (they already raise `DocumentParsingError` on empty output), and `__all__ = ["DocumentParsingError", "ExtractionOutcome", "PARSE_QUALITY_DOCLING", "PARSE_QUALITY_PREVIEW", "extract_document_text"]`.

Delete `document_parser_tier` from `config.py` (line 69) and any test referencing it.

- [ ] **Step 4: Thread `ExtractionOutcome` through the workflow**

In `pdf_workflow.py`:
- `ExtractTextFn = Callable[[str], ExtractionOutcome]` (import `ExtractionOutcome` from parsing).
- At the `text_extracted` state, store `{"pages": …, "parse_quality": outcome.parse_quality}` in the checkpoint payload (extend `_pages_from_checkpoint_payload` at `pdf_workflow.py:1024` with a sibling `_parse_quality_from_checkpoint_payload(payload) -> str` that defaults to `"preview"` for old checkpoints).
- At the chunked state, pass `parse_quality=` through to `replace_document_chunks`.
- `default_pdf_ingestion_dependencies` keeps `extract_text=extract_document_text` (signature now returns the outcome).

Fix all workflow tests that stub `extract_text` to return bare page lists — wrap their stubs in `ExtractionOutcome(pages=…, parse_quality="docling")`.

- [ ] **Step 5: Run the full affected suites**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_document_parsing.py tests/test_pdf_workflow.py tests/test_documents_api.py tests/test_document_store.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A apps/server
git commit -m "feat(documents): Docling-always parsing; delete tiers and escalation heuristics"
```

---

### Task 6: reparse upgrade path (preview/legacy → docling)

**Files:**
- Create: `apps/server/src/anima_server/services/documents/reparse.py`
- Modify: `apps/server/src/anima_server/api/routes/documents.py` (POST `/documents/{document_id}/reparse` + GET `/documents/parsing-pack` status/ensure endpoints)
- Test: `apps/server/tests/test_document_reparse.py`

**Interfaces:**
- Consumes: `extract_document_text` (Task 5 — must return `parse_quality="docling"`, otherwise reparse is a no-op), `chunk_pages_structured`, `replace_document_chunks` (Task 3), `embed_document_chunks` from `indexing.py`, `sync_document_source` from `ingestion/adapters/documents.py`, `resolve_document_storage_path` from `store.py`.
- Produces: `reparse_document(runtime_db, *, user_id, document_id, embedding_fn=None) -> ReparseResult` with `ReparseResult = dataclass(status: str, chunk_count: int)`, `status ∈ {"upgraded", "pack_not_ready", "not_found"}`; `list_reparse_candidates(runtime_db, *, user_id) -> list[int]` (documents with `parse_quality != "docling"` and status `"indexed"`). Plan 3 wires these to the sleep agent and OKF recompile queue.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_document_reparse.py
from __future__ import annotations

import pytest
from anima_server.services.documents import reparse
from anima_server.services.documents.parsing import ExtractionOutcome
from anima_server.services.documents.pdf_text import PageText

# Reuse the ingestion fixtures/factories used by tests/test_pdf_workflow.py to
# create an indexed document with parse_quality="preview" — mirror its setup.


def test_reparse_upgrades_preview_document(runtime_db, preview_document, monkeypatch) -> None:
    monkeypatch.setattr(
        reparse,
        "extract_document_text",
        lambda path: ExtractionOutcome(
            pages=[PageText(page_number=1, text="# Section\n\nUpgraded body")],
            parse_quality="docling",
        ),
    )

    result = reparse.reparse_document(
        runtime_db,
        user_id=preview_document.user_id,
        document_id=preview_document.id,
        embedding_fn=lambda text: [0.1, 0.2, 0.3],
    )

    assert result.status == "upgraded"
    assert preview_document.parse_quality == "docling"
    assert preview_document.status == "indexed"


def test_reparse_noop_when_pack_not_ready(runtime_db, preview_document, monkeypatch) -> None:
    monkeypatch.setattr(
        reparse,
        "extract_document_text",
        lambda path: ExtractionOutcome(
            pages=[PageText(page_number=1, text="still preview")],
            parse_quality="preview",
        ),
    )

    result = reparse.reparse_document(
        runtime_db,
        user_id=preview_document.user_id,
        document_id=preview_document.id,
    )

    assert result.status == "pack_not_ready"
    assert preview_document.parse_quality == "preview"


def test_list_reparse_candidates_returns_non_docling_indexed(runtime_db, preview_document) -> None:
    assert reparse.list_reparse_candidates(
        runtime_db, user_id=preview_document.user_id
    ) == [preview_document.id]
```

(`preview_document` is a fixture the implementer builds from the same factory helpers `tests/test_pdf_workflow.py` uses to create an indexed document, then sets `parse_quality="preview"` on the document row.)

- [ ] **Step 2: Run to verify failure**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_document_reparse.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `reparse.py`**

```python
# src/anima_server/services/documents/reparse.py
"""Upgrade preview/legacy documents to Docling-quality artifacts.

Reparse re-extracts through the canonical parser, re-cuts chunks along real
section structure, re-embeds, and re-syncs source spans — then stamps the
document ``parse_quality="docling"``. Only docling-quality output is ever
written; if the pack is not ready the document is left untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.models.runtime import RuntimeDocument
from anima_server.services.documents.chunking import chunk_pages_structured
from anima_server.services.documents.indexing import EmbeddingFn, embed_document_chunks
from anima_server.services.documents.parsing import (
    PARSE_QUALITY_DOCLING,
    extract_document_text,
)
from anima_server.services.documents.store import (
    get_document_for_user,
    replace_document_chunks,
    resolve_document_storage_path,
)
from anima_server.services.ingestion.adapters.documents import sync_document_source

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReparseResult:
    status: str  # "upgraded" | "pack_not_ready" | "not_found"
    chunk_count: int = 0


def reparse_document(
    runtime_db: Session,
    *,
    user_id: int,
    document_id: int,
    embedding_fn: EmbeddingFn | None = None,
) -> ReparseResult:
    document = get_document_for_user(runtime_db, user_id=user_id, document_id=document_id)
    if document is None:
        return ReparseResult(status="not_found")

    path = resolve_document_storage_path(document)
    outcome = extract_document_text(str(path))
    if outcome.parse_quality != PARSE_QUALITY_DOCLING:
        return ReparseResult(status="pack_not_ready")

    chunks = chunk_pages_structured(outcome.pages)
    rows = replace_document_chunks(
        runtime_db,
        document_id=document.id,
        chunks=chunks,
        parse_quality=PARSE_QUALITY_DOCLING,
    )
    embed_document_chunks(
        runtime_db, user_id=user_id, document_id=document.id, embedding_fn=embedding_fn
    )
    document.status = "indexed"
    sync_document_source(runtime_db, user_id=user_id, document_id=document.id)
    runtime_db.flush()
    logger.info("Reparsed document %s: %d docling chunks", document.id, len(rows))
    return ReparseResult(status="upgraded", chunk_count=len(rows))


def list_reparse_candidates(runtime_db: Session, *, user_id: int) -> list[int]:
    stmt = (
        select(RuntimeDocument.id)
        .where(
            RuntimeDocument.user_id == user_id,
            RuntimeDocument.status == "indexed",
            RuntimeDocument.parse_quality != PARSE_QUALITY_DOCLING,
        )
        .order_by(RuntimeDocument.id)
    )
    return list(runtime_db.scalars(stmt).all())


__all__ = ["ReparseResult", "list_reparse_candidates", "reparse_document"]
```

The implementer MUST check the real signatures of `resolve_document_storage_path`, `embed_document_chunks`, and `sync_document_source` before wiring (they exist today — match their exact parameter names; `embed_document_chunks` handles `embedding_fn=None` by using the default provider).

- [ ] **Step 4: API endpoints**

In `api/routes/documents.py`, following the file's existing route/response conventions:
- `GET /documents/parsing-pack` → `{"state": status.state, "progress": status.progress, "error": status.error}` from `pack_status()`.
- `POST /documents/parsing-pack/download` → calls `ensure_parsing_pack()`, returns same shape.
- `POST /documents/{document_id}/reparse` → calls `reparse_document(...)` for the authenticated user, 404 on `not_found`, 409 with detail `"Parsing pack is not ready."` on `pack_not_ready`, else `{"status": "upgraded", "chunk_count": n}`.

Add API tests in `tests/test_documents_api.py` mirroring its existing route-test style (auth fixture, status-code + payload assertions) for all three endpoints, monkeypatching `pack_status`/`ensure_parsing_pack`/`reparse_document` at the route module.

- [ ] **Step 5: Run tests**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest tests/test_document_reparse.py tests/test_documents_api.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite + commit**

Run: `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run --project . pytest -q` — expected all green, and `uv run --project . ruff check src tests` clean.

```bash
git add -A apps/server
git commit -m "feat(documents): reparse upgrade path and parsing-pack API"
```

---

## Deferred to Plan 2 (retrieval quality)

Bundled fastembed default embedding provider; full-doc context mode with effective-context threshold; ONNX reranker; contextual blurbs/reranker defaults-on.

## Deferred to Plan 3 (trust layer)

Capability status endpoint + desktop UI surfacing; OKF compiler quality gate (`parse_quality="docling"` spans only) + recompile-on-reparse; sleep-agent wiring of `list_reparse_candidates`; dev scripts `--all-extras` + README; golden corpus + real-embedding evals + CI matrix.
