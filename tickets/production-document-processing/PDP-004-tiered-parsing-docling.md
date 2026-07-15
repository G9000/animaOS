# PDP-004 - Tiered Parsing: Docling Quality Tier and OCR Fallback

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `PDP-000`
- Depends on: `PDP-003`
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-11 (implemented in worktree `production-doc-processing`)

## Goal

Add Docling (MIT) as an optional, lazy-loaded quality parsing tier for PDFs (and later DOCX/PPTX/XLSX), with OCR fallback so scanned PDFs stop hard-failing. Keep the pypdf fast path as the default for born-digital simple documents.

## Design

- **Optional dependency.** `docling` under an extras group (`anima-server[docling]`) plus a settings flag (`DOCUMENT_PARSER_TIER=auto|fast|quality`). Import lazily inside the parser module; absence of the extra degrades to fast path with a structured log/warning, never an import error at startup.
- **Escalation policy (`auto`).** Run pypdf first; score the output: extractable text density per page, heading detectability, table markers, pages with near-zero text (scan suspicion). Below threshold → escalate to Docling for the whole document (per-page escalation is a later optimization). Policy lives in one module with unit-testable scoring.
- **Docling conversion.** `DocumentConverter` → `DoclingDocument` → map headings/paragraphs/tables (+ page/bbox provenance) into the `StructuredDocument` intermediate from PDP-003. Tables exported as markdown tables, atomic chunks.
- **OCR.** Enable Docling's OCR (RapidOCR or Tesseract backend) for scan-suspect pages. Scanned PDFs: succeed with the extra installed; without it, fail with an explicit "install the docling extra or provide a text PDF" error instead of the current generic no-text failure.
- **Model assets.** First-use model download handled inside the workflow with progress surfaced in workflow status; document an offline pre-fetch command. Models load per-ingestion and release after (no resident memory cost).
- **Workflow integration.** The `text_extracted` state records which tier produced the artifact; escalation is deterministic on resume (same input → same tier), keeping checkpoints idempotent.

## Non-goals

- VLM escalation tier (future ticket if lint/evals show residual bad parses). DOCX/PPTX enablement (fast follow — Docling supports them; needs upload-route MIME allowlist and tests).

## Acceptance

- Complex-layout PDF (multi-column + tables) parses with headings and intact tables under the quality tier; same file under fast path shows measurably degraded structure (fixture-based comparison test).
- Scanned PDF ingests successfully with the extra installed; clear actionable error without it.
- Server with base install (no extra) passes the full existing test suite unchanged.
- Escalation scoring covered by unit tests; Docling-dependent tests marked/skipped when the extra is absent (CI job matrix: with and without extra).

## Validation

- Commands: server test suite in both dependency configurations, ruff, one manual ingest of a scanned PDF and a table-heavy PDF.
- Changed paths: `apps/server/pyproject.toml`, `apps/server/src/anima_server/services/documents/` (parser tiering), `apps/server/src/anima_server/services/ingestion/adapters/documents.py`, tests.

## Activity Log

- 2026-07-11 - Implemented (Claude). New `services/documents/parsing.py`: `extract_document_text` tiered entry point (settings `document_parser_tier=auto|fast|quality`, default auto), deterministic escalation scoring (`should_escalate_extraction`: >=50% of pages under 15 words), scanned-PDF no-text errors escalate to Docling OCR when available or raise an actionable `DocumentParsingError` naming the extra when not. `_convert_with_docling` is the single Docling-touching function: lazy imports, `do_ocr=True`, per-page markdown via form-feed page breaks, models loaded per call.
- PDF workflow defaults switched: `extract_text=extract_document_text`, `chunk_text=chunk_pages_structured` (new in `chunking.py`) -- pages (pypdf plain text or Docling markdown) go through `structure_pages_markdown` + `chunk_structured_document`, populating `runtime_document_chunks.section_title` from heading paths. Oversized-section split parts now carry per-part page/line ranges for citation fidelity.
- `pyproject.toml` gains the `docling` optional extra (`uv sync --extra docling`); uv.lock resolved. Base install unchanged.
- Deviations from ticket: model pre-download command and workflow-status download progress deferred (follow-up; first Docling use downloads models inline); CI matrix job for the with-extra config not added (no CI config in repo scope); DOCX/PPTX remain non-goals.
- Validation: new `tests/test_document_parsing.py` (13 tests: tier routing, escalation thresholds, scanned-PDF paths with/without extra, error passthrough, empty-OCR failure, section-title chunking for both markdown and plain pages); full server suite 2138 passed; ruff clean. PENDING: one manual end-to-end run with the docling extra installed against a real scanned + table-heavy PDF (extra not installed in the dev env; Docling boundary covered via the `_convert_with_docling` seam).
