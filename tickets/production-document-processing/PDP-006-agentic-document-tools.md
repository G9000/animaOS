# PDP-006 - Agentic Document Tools

- Status: backlog
- Priority: P1
- Scope: `apps/server`
- Parent: `PDP-000`
- Depends on: `PDP-002`
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-12 (implemented in worktree `production-doc-processing`)

## Goal

Let the agent investigate documents iteratively instead of answering from a fixed one-shot injection. This is the largest gap versus current practice: the model today cannot request more context than the initial 5-chunk block.

## Design

New agent tools registered alongside the existing tool surface (`services/agent/tools.py`), scoped to the turn's user and honoring the same visibility rules as the REST routes:

- `search_documents(query, document_ids=None, limit=8)` — hybrid retrieval (PDP-002) over indexed chunks; when `document_ids` omitted, defaults to the active thread documents, with an explicit `scope="all"` escape hatch for the user's whole library.
- `get_document_outline(document_id)` — the section tree (section paths + page ranges) from structured spans, falling back to per-page outline for legacy documents.
- `read_document_section(document_id, section_path=None, page_start=None, page_end=None)` — returns the parent-section or page-range text, bounded (~6k chars per call) with continuation support.

Guardrails:

- Per-turn budget: cap total tool-fetched document text (config, e.g. ~40k chars) so the loop cannot blow the context window; over-budget calls return a truncation notice.
- The existing injected document context block shrinks to a lightweight first-turn primer (document titles + top hits + a hint that tools exist); PDP-001 limits apply until then.
- Tool calls recorded in the existing trace/pill machinery: cited sections feed `document_source` pills so provenance UX keeps working.
- Prompt directive updated: prefer tools for depth, injected primer for orientation; no English keyword routing (consistent with the routing contract).

## Acceptance

- Agent answers a question requiring content outside the initial primer by calling `search_documents`/`read_document_section` (integration test with scripted model).
- Outline tool returns correct tree for a structured document and page fallback for a legacy one.
- Budget cap enforced; over-cap read returns truncation notice, not an error.
- Ownership enforced: tools refuse documents not owned by the turn's user.
- `test_agent_service.py` document-grounding suites updated and green; pills still emitted for tool-sourced citations.

## Validation

- Commands: server test suite (agent + tools subsets), ruff, one manual end-to-end chat against a large PDF.
- Changed paths: `apps/server/src/anima_server/services/agent/tools.py`, `apps/server/src/anima_server/services/agent/service.py`, tests.

## Activity Log

- 2026-07-12 - Implemented (Claude session):
  - New `services/agent/document_tools.py` registered in `get_core_tools()`: `search_documents` (hybrid PDP-002 retrieval; defaults to active thread documents, `scope="all"` escape hatch, comma-separated `document_ids` override), `get_document_outline` (section tree from chunk `section_title` paths with page ranges/sizes; per-chunk page fallback for legacy documents), `read_document_section` (by `section_path`, page range, or sequential; bounded per call by `document_tool_read_char_limit` with `start_chunk` continuation).
  - Guardrails: per-turn budget `document_tool_turn_char_budget` (40k default) accounted on `ToolContext.document_tool_chars_used`; over-budget calls return a truncation notice, never an error; all lookups go through `get_document_for_user` so unowned documents read as nonexistent.
  - Provenance: tools record citations on `ToolContext.document_tool_citations`; `_capture_document_tool_citations` folds them into `document_source` pills before the tool context clears (deduped against injected-context pills), so tool-driven citations emit pills even on turns that started without a document block.
  - Primer: `_build_document_context_block` now appends the selected-documents list and a tool hint; the turn directive tells the model to investigate with the tools before declaring content missing (no keyword routing).
  - Tests: new `test_document_tools.py` (outline tree + legacy fallback, section/page reads, continuation, budget cap, ownership refusal, hybrid search delegation + thread-default/scope-all, citation→pill fold with dedup). Agent service/system-prompt/rules/runtime/executor suites green, ruff clean.
  - Deferred: approval-resume path does not capture tool citations into pills (main turn path only); manual large-PDF end-to-end check pending with the rest of the epic (PDP-009).
