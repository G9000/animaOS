# PDP-004 - Tiered Parsing: Docling Quality Tier and OCR Fallback

- Status: todo
- Priority: P1
- Scope: `apps/server`
- Parent: `PDP-000`
- Depends on: `PDP-003`
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-10

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
