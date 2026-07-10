# PDP-005 - HTML and Web Capture Extraction

- Status: todo
- Priority: P1
- Scope: `apps/server`
- Parent: `PDP-000`
- Depends on: `PDP-003`
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-10

## Goal

Make HTML a first-class ingested format. Today the web-capture adapter stores caller-provided readable text — the extraction burden is on the client. Move real extraction server-side with trafilatura.

## Design

- **Dependency.** `trafilatura` (Apache-2.0-compatible stack) in base dependencies — it is lightweight, unlike the Docling tier.
- **Input modes.** `POST /api/knowledge/sources/web-capture` accepts (a) raw HTML bytes/string + URL metadata (primary: the desktop or a connector supplies captured HTML), or (b) the existing pre-extracted readable text (kept for compatibility). Server-side URL fetching is a separate opt-in flag with SSRF guards (deny private address ranges, size/time limits) — default off for the local-first threat model.
- **Extraction.** trafilatura extracts readable content with structure (headings, paragraphs, lists) and metadata (title, canonical URL, author, date) → mapped into the `StructuredDocument` intermediate → structure-aware spans via the PDP-003 chunker. Raw HTML retained as an artifact (`kind="raw_html"`) so extraction is re-runnable when the extractor improves.
- **Uploads.** Allow `.html` file upload through the documents/knowledge upload surface with MIME validation, same size limits as PDFs.

## Acceptance

- Ingesting a saved article HTML file yields spans with correct section paths, title metadata, and no nav/boilerplate content (fixture assertions).
- Existing pre-extracted-text captures keep working (compatibility test).
- URL fetch mode, when enabled, refuses private-range and oversized targets (unit tests, no network in CI).
- Re-running extraction on stored raw HTML replaces spans idempotently.

## Validation

- Commands: server test suite (ingestion + knowledge routes subsets), ruff.
- Changed paths: `apps/server/pyproject.toml`, `apps/server/src/anima_server/services/ingestion/adapters/web.py`, `apps/server/src/anima_server/api/routes/knowledge.py`, tests.
