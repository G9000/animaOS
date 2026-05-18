# Memory Recall Reliability Todo

workstream: `scratchboard/v2-memory-recall-reliability`
plan: `docs/superpowers/plans/2026-05-17-memory-recall-improvements.md`
created: 2026-05-17

## Phase 1: Reliability Hardening

- [x] MR-001: Decide and document the runtime memory store architecture.
  - Context: Repo guidance says SQLite + SQLCipher, but runtime memory currently uses PostgreSQL/pgvector for candidates, runtime messages, access logs, and embeddings.
  - Outcome: Documented decision is to keep embedded PostgreSQL as the local runtime store/cache while SQLCipher remains the durable memory source of truth.

- [x] MR-002: Fix Soul Writer session ownership during inline embedding.
  - Context: `soul_writer.py` passes a SQLAlchemy `soul_db` session from a worker thread into an async event-loop coroutine.
  - Outcome: Promotion, embedding, SQLCipher writes, vector upsert, and Rust index sync no longer share DB sessions across threads.
  - Note: `_embed_and_index_item` now opens its own soul session from a factory and commits embedding/index updates separately from promotion.

- [x] MR-003: Make explicit saves visible immediately.
  - Context: `save_to_memory` writes `MemoryCandidate` rows first; canonical `MemoryItem` visibility waits for Soul Writer.
  - Outcome: User-explicit saves are visible in the same turn or the next memory refresh without relying on a later background pass.
  - Note: Pending `user_explicit` candidates now render in `pending_memory_updates` until Soul Writer promotes them.

- [x] MR-004: Invalidate BM25 and other retrieval indexes on every memory mutation.
  - Context: Some write paths update Rust/vector state, while BM25 cache invalidation is tied to embedding/upsert paths.
  - Outcome: Add/update/supersede/delete paths consistently mark keyword and vector retrieval views stale.
  - Note: Canonical `MemoryItem` rows now seed Python BM25 rebuilds, and direct memory writes invalidate BM25 plus the Rust memory-index dirty marker.

- [x] MR-005: Add memory pipeline health checks.
  - Context: Current health checks do not expose candidate backlog, failed candidates, embedding coverage, stale access logs, or dirty retrieval indexes.
  - Outcome: `/health` reports actionable memory pipeline status and degradation causes.
  - Note: Added `memory_pipeline` to the default health registry with candidate/pending-op backlog, failed/retry-exhausted work, embedding coverage, stale access-log/retrieval-feedback sync, and dirty retrieval-index signals.

- [x] MR-006: Preserve failed extraction work for retry.
  - Context: LLM extraction failure currently logs that facts from the turn are lost.
  - Outcome: Failed extraction is represented as retryable work with source message IDs and visible health state.
  - Note: Added a runtime `memory_extraction_failures` queue, records failed LLM extraction attempts with source message IDs/previews/error state, and surfaces retryable/exhausted failures in `memory_pipeline` health.

## Phase 2: Recall Quality

- [x] MR-101: Implement `search_long_memory(query, mode)` as an agent tool.
  - Context: Existing plan exists at `docs/superpowers/plans/2026-05-03-long-memory-evidence-tool.md`.
  - Outcome: Agent can intentionally search wide evidence for counts, latest values, temporal ordering, and preference-driven recommendations.
  - Note: Added the `search_long_memory` core tool, explicit `retrieve_wide_evidence(mode=...)` behavior, and system prompt guidance; the legacy pre-turn classifier remains only until MR-102 removes that path.

- [x] MR-102: Remove the pre-turn English keyword classifier.
  - Context: `retrieval_intent.py` is brittle and language-specific.
  - Outcome: Wide retrieval is tool-driven and visible in traces, not hidden in pre-turn prompt assembly.
  - Note: Removed the classifier module, pre-turn wide-evidence service helper, `evidence_memories` prompt block, and classifier-specific tests. `retrieve_wide_evidence` now requires an explicit mode from `search_long_memory`.

- [x] MR-103: Fix hybrid search under-fill after filtering.
  - Context: `hybrid_search` resolves `merged[:limit]` before heat/tag/missing-item filters, so valid later results can be skipped.
  - Outcome: Retrieval backfills from later ranked candidates until the requested limit or candidate pool is exhausted.
  - Note: Hybrid search now resolves the merged candidate pool before post-filtering and walks later ranked IDs until the requested limit is filled.

- [x] MR-104: Improve exact conversation and transcript recall.
  - Context: `recall_conversation` is text-only over recent runtime messages; `recall_transcript` defaults to 30 days and is weakly prompted.
  - Outcome: The agent reliably reaches active messages, archived transcripts, and long memory for old exact-recall questions.
  - Note: `recall_transcript` now defaults to all archived transcripts (`days_back=0`), transcript search treats non-positive `days_back` as all-time, and the prompt routes exact old-chat questions to `recall_conversation` or all-time transcript search.

- [x] MR-105: Add a recall regression probe suite.
  - Context: Long-memory behavior has benchmark work, but pipeline health and exact recall need stable probes.
  - Outcome: Focused tests or eval probes cover explicit save, candidate fallback, keyword recall, semantic recall, latest/count recall, and transcript recall.
  - Note: Added `test_memory_recall_regressions.py` covering explicit-save recall before promotion, candidate fallback, keyword fallback, hybrid semantic results, latest-mode long-memory evidence, and all-time transcript recall.

- [x] MR-106: Update prompt guidance for recall tool choice.
  - Context: The current system prompt mentions `recall_memory`, but not enough about when to use transcript or long-memory evidence.
  - Outcome: The agent has concise rules for choosing visible memory, `recall_memory`, `recall_conversation`, `recall_transcript`, and `search_long_memory`.
  - Note: Consolidated recall guidance into one prompt rule covering visible memory first, durable memory, active conversation history, all-time archived transcript search, and cross-session long-memory evidence.

## Phase 3: Structured Provenance And Event Memory

- [x] MR-201: Design a source/evidence schema for durable memories.
  - Context: Atomic `MemoryItem` rows lack enough structured provenance for reliable latest/count/temporal questions.
  - Outcome: PRD or design note defines source thread, source message, observed date, speaker, confidence, and evidence relationships.
  - Note: Added `docs/prds/memory/provenance-and-event-memory.md` with the decision to add a dedicated durable `memory_item_evidence` table instead of extending claim-scoped evidence.

- [x] MR-202: Implement source/evidence models and migration.
  - Context: `MemoryClaimEvidence` exists conceptually, but retrieval and prompt assembly do not consistently use source evidence.
  - Outcome: Durable models and migration support evidence-backed recall without relying on raw eval chunks.
  - Note: Added `MemoryItemEvidence`, registered it in the memories encryption domain, created core migration `20260517_0001_create_memory_item_evidence.py`, and verified Alembic upgrade/current against a temporary SQLite core DB.

- [x] MR-203: Attach provenance in all memory write paths.
  - Context: Explicit saves, extraction candidates, transcript imports, and eval imports need consistent source metadata.
  - Outcome: Promoted memories know where they came from and when the user said them.
  - Note: Explicit saves now retain latest user message IDs, regex/LLM candidates carry source message IDs, Soul Writer writes `memory_item_evidence` during promotion/supersession, eval transcript imports pass source IDs into extraction, raw eval chunks create evidence rows, and eval reset purges evidence rows for disposable users.

- [x] MR-204: Add metadata-aware retrieval for latest/count/temporal questions.
  - Context: Wide evidence currently parses dates out of text chunks.
  - Outcome: Retrieval can answer time-sensitive questions from structured metadata.
  - Note: `retrieve_wide_evidence` now expands `memory_item_evidence` rows, formats observed dates into evidence snippets, ranks latest-update evidence by `observed_at`, orders temporal evidence chronologically, and preserves distinct evidence rows for aggregate/count-style searches.

- [x] MR-205: Backfill or bridge existing memory data.
  - Context: Existing atomic memories and eval raw chunks use different shapes.
  - Outcome: Migration/backfill strategy preserves existing recall while improving future writes.
  - Note: Added an idempotent `backfill_memory_item_evidence` helper that creates `legacy_backfill` evidence for existing memories without provenance, bridges old `eval_import_raw` chunks into `eval_import` evidence, parses `Session date:` into `observed_at`, and skips items that already have evidence.

- [x] MR-206: Reconcile architecture docs with implementation.
  - Context: Docs disagree about SQLite-only storage, PostgreSQL runtime state, and recall internals.
  - Outcome: `docs/architecture/memory/` and `docs/architecture/agent/` match the shipped system.
  - Note: Updated architecture overview, memory system, agent runtime, F15 PRD status, and docs changelog for the dual SQLCipher/PostgreSQL store, source provenance, tool-driven long-memory recall, metadata-aware retrieval, extraction failure preservation, and legacy/eval backfill. `docs-code-sync` still reports 215 repo-wide pre-existing broken link/path issues outside this workstream after touched-file link fixes.

## Validation Gates

- [x] Run focused server tests for each touched subsystem.
- [x] Run `bun run test:server` after backend changes.
- [x] Run `bun run lint`.
- [x] Run `bun run build`.
- [x] Smoke-test auth, chat, memory, settings, and `GET /health`.
  - Note: Focused route smoke passed with `uv run pytest apps/server/tests/test_auth.py apps/server/tests/test_chat.py apps/server/tests/test_memory_api.py apps/server/tests/test_health.py apps/server/tests/test_health_api.py apps/server/tests/test_security_hardening.py::test_config_update_blocked_in_shared_mode apps/server/tests/test_security_hardening.py::test_config_update_allowed_in_sqlite_mode -q` (46 passed). Full server tests, lint, build, `bun run db:server:current`, and core Alembic head check passed; `db:server:current` needed sandbox escalation in an earlier pass after uv cache access was denied.
