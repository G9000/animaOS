# PDP-001 - Chat Grounding Quick Wins: Injection Limits and Chunk Overlap

- Status: in_progress
- Priority: P0
- Scope: `apps/server`
- Parent: `PDP-000`
- Depends on: none
- Owner: unassigned
- Created: 2026-07-10
- Updated: 2026-07-10 (implemented in worktree `production-doc-processing`)

## Goal

Stop starving the model of document context. Today a document-grounded turn sees at most 5 chunks truncated to 900 chars each (~4.5KB of a possibly 150KB document). Fix the numbers now, ahead of the larger retrieval rework.

## Problem

- `_build_document_context_block` (`apps/server/src/anima_server/services/agent/service.py:1763`) calls `search_document_chunks(..., limit=5)`.
- Retrieved chunks are truncated to 900 chars by `_truncate_document_chunk` (`service.py:2102`) — half of the deliberate 1800-char chunk size.
- `chunk_pages` (`services/documents/chunking.py`) has overlap disabled, so facts straddling chunk boundaries are split and rank poorly.

## Work

1. Raise retrieval to 15 chunks (config-backed constant, not hardcoded) and pass full chunk text through — no 900-char truncation on raw evidence. Keep a generous safety cap (e.g. 2500 chars) only to bound pathological chunks.
2. Verify prompt-budget behavior (`test_prompt_budget.py`): document context keeps its priority; budget compaction, not silent truncation, handles overflow.
3. Enable chunk overlap in `chunk_pages` (~200 chars / ~10%). Note: content hashes change, so re-ingested documents replace chunks via the existing `replace_document_chunks()` path; existing indexed documents are unaffected until re-ingestion.
4. Keep the compiled-knowledge section of the block as-is (reworked in PDP-007).

## Acceptance

- Document-grounded turn context contains up to 15 untruncated chunks.
- Overlap present in newly ingested documents' chunks; `chunk_index`/page ranges remain consistent.
- `test_chat_document_context.py`, `test_document_chunking.py`, `test_prompt_budget.py`, `test_agent_service.py` updated and green.
- Manual check: a question answerable only from mid-document content in a 40+ page PDF now answers correctly.

## Validation

- Commands: server test suite (documents + agent context subsets), ruff.
- Changed paths: `apps/server/src/anima_server/services/agent/service.py`, `apps/server/src/anima_server/services/documents/chunking.py`, tests.

## Activity Log

- 2026-07-10 - Implemented (Claude): retrieval limit 5 -> `_DOCUMENT_CONTEXT_CHUNK_LIMIT = 15`, truncation cap 900 -> `_DOCUMENT_CHUNK_CHAR_CAP = 2500` (`services/agent/service.py`); chunk overlap enabled with 200-char word-boundary tail carried across chunks, `overlap_chars` default 0 -> 200 (`services/documents/chunking.py`). Bonus fix: `_raw_document_results_without_compiled_coverage` now fails open (returns raw results) when the compiled-coverage lookup errors, instead of silently dropping all evidence.
- Validation: `uv run pytest tests/test_document_chunking.py tests/test_chat_document_context.py tests/test_prompt_budget.py tests/test_pdf_workflow_checkpoints.py tests/test_document_rag.py tests/test_documents_api.py tests/test_agent_service.py` all green; ruff clean on changed files.
