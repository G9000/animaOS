# PDP-005 - HTML and Web Capture Extraction

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `PDP-000`
- Depends on: `PDP-003`
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-11 (implemented in worktree `production-doc-processing`)

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

## Activity Log

- 2026-07-11 - Implemented (Claude session):
  - `trafilatura>=2.0` added to base dependencies; `services/ingestion/html_extract.py` wraps it (lazy import) and returns markdown + title/author/date/canonical-url metadata; extraction failure raises a clear ValueError.
  - `adapters/web.py`: `ingest_web_capture` now takes exactly one of `readable_text` (legacy, unchanged shape) or `html`; the HTML path stores a `raw_html` artifact plus a `structured_markdown` artifact with outline, and spans come from the PDP-003 structured pipeline (heading/paragraph evidence spans + parent section spans with section paths). New `ingest_html_content` for `.html` uploads (`html://<filename>` sources) and `reextract_source_html` to re-run extraction from the stored raw HTML (span rows stable when output unchanged).
  - `services/ingestion/web_fetch.py`: opt-in server-side URL fetch (`ANIMA_WEB_CAPTURE_URL_FETCH_ENABLED`, default off) with SSRF guards — http(s) only, private/loopback/link-local/reserved ranges refused on every redirect hop, content-type restricted to HTML, size (5MB default) and time limits.
  - Routes: `/sources/web-capture` accepts `html` or `fetch: true` (403 when fetching disabled, 413 over the PDF-parity size limit); new `POST /sources/html` multipart upload with MIME validation; new `POST /sources/{id}/reextract`.
  - Tests: `test_html_ingestion.py` (extraction, boilerplate stripping, section paths, compat mode, idempotent re-extract), `test_web_fetch.py` (SSRF guards, redirects, size/type limits — no network), knowledge API additions. Validation: ingestion + knowledge suites green, ruff clean.
