# Memory Recall Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ANIMA's memory pipeline reliable, observable, and better at recalling facts across time, sessions, and exact transcripts.

**Architecture:** Treat the current system as three layers: durable soul memory in SQLCipher, runtime recall infrastructure, and agent-facing recall tools. Phase 1 removes reliability hazards and stale-index risks. Phase 2 improves tool-driven recall quality. Phase 3 adds structured provenance so latest/count/temporal recall does not depend on raw text chunks.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, SQLCipher SQLite, embedded/runtime PostgreSQL where currently used, pgvector, Rust retrieval bindings, pytest, ruff, Bun/Nx scripts.

---

## Tracking

- Workstream todo: `scratchboard/v2-memory-recall-reliability/todo.md`
- System tracker: `scratchboard/_system/active-tasks.md`
- Audit source: static code audit performed on 2026-05-17
- Related prior plan: `docs/superpowers/plans/2026-05-03-long-memory-evidence-tool.md`
- Related prior plan: `docs/superpowers/plans/2026-04-01-memory-pipeline-reliability.md`

## File Map

| Area | Files |
| --- | --- |
| Memory write path | `apps/server/src/anima_server/services/agent/tools.py`, `consolidation.py`, `soul_writer.py`, `candidate_ops.py`, `memory_store.py` |
| Retrieval path | `embeddings.py`, `bm25_index.py`, `evidence_retrieval.py`, `retrieval_intent.py`, `conversation_search.py`, `transcript_search.py`, `memory_blocks.py` |
| Runtime state | `models/runtime_memory.py`, `models/runtime_embedding.py`, `db/runtime.py`, `api/routes/chat.py` |
| Health | `services/health/checks.py`, `services/health/registry.py` |
| Prompts | `services/agent/templates/system_prompt.md.j2` |
| Tests | `apps/server/tests/test_soul_writer.py`, `test_memory_candidates.py`, `test_bm25_index.py`, `test_evidence_retrieval.py`, `test_agent_wide_evidence_wiring.py`, new focused recall tests |

## Phase 1: Reliability Hardening

### Ticket MR-001: Runtime Memory Store Decision

**Problem:** Project guidance says SQLite + SQLCipher, but runtime memory uses PostgreSQL/pgvector for runtime messages, candidates, access logs, and embeddings.

**Files:**
- Review: `apps/server/src/anima_server/db/runtime.py`
- Review: `apps/server/src/anima_server/models/runtime_memory.py`
- Review: `apps/server/src/anima_server/models/runtime_embedding.py`
- Modify: `docs/architecture/memory/memory-system.md`
- Modify: `docs/architecture/agent/agent-runtime.md`
- Modify if needed: `AGENTS.md`

- [x] Confirm whether embedded PostgreSQL is an intentional runtime cache or an architecture violation.
- [x] Document the decision and failure mode.
- [x] If keeping PostgreSQL, document startup requirements and degraded behavior.
- [x] If migrating away, split a dedicated migration plan before code changes. Not needed; decision keeps embedded PostgreSQL as the runtime store/cache.

### Ticket MR-002: Soul Writer Session Boundary

**Problem:** `soul_writer.py` promotes memory in a worker thread and schedules async embedding/indexing on the event loop while passing a SQLAlchemy session across the boundary.

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/soul_writer.py`
- Test: `apps/server/tests/test_soul_writer.py`
- Test: `apps/server/tests/test_memory_pipeline_reliability.py`

- [x] Write a regression test that verifies embedding/indexing does not receive a worker-owned SQLAlchemy session.
- [x] Refactor `_embed_and_index_item` so DB writes happen inside a session owned by the executing thread.
- [x] Keep promotion commits independent from embedding failure.
- [x] Verify failed embedding still leaves a promoted memory for later backfill.
- [x] Run focused Soul Writer tests.

### Ticket MR-003: Explicit Save Visibility

**Problem:** `save_to_memory` returns success after creating a `MemoryCandidate`, but canonical memory blocks may not include the fact until Soul Writer runs.

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Modify as needed: `apps/server/src/anima_server/services/agent/memory_blocks.py`
- Modify as needed: `apps/server/src/anima_server/services/agent/service.py`
- Test: `apps/server/tests/test_agent_tools.py`
- Test: `apps/server/tests/test_memory_retrieval_rebuild.py`

- [x] Decide whether user-explicit saves promote synchronously or appear through a pending-explicit-memory block.
- [x] Add a same-turn or next-refresh visibility test.
- [x] Implement the minimal path that makes explicit saves visible without duplicating durable memory.
- [x] Ensure memory cache invalidation still works.

### Ticket MR-004: Retrieval Index Invalidation

**Problem:** New or changed memories can update one retrieval surface while leaving the cached BM25 keyword index stale.

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/memory_store.py`
- Modify: `apps/server/src/anima_server/services/agent/bm25_index.py`
- Modify as needed: `apps/server/src/anima_server/services/agent/vector_store.py`
- Test: `apps/server/tests/test_bm25_index.py`
- Test: `apps/server/tests/test_memory_store.py`

- [x] Identify every add/update/supersede/delete memory mutation path.
- [x] Add BM25 invalidation to each mutation path.
- [x] Add tests proving a cached index is rebuilt after a direct memory write.
- [x] Verify embedding failure does not leave keyword recall stale.

### Ticket MR-005: Memory Pipeline Health Checks

**Problem:** `/health` does not expose memory pipeline degradation.

**Files:**
- Modify: `apps/server/src/anima_server/services/health/checks.py`
- Modify: `apps/server/src/anima_server/services/health/registry.py`
- Test: `apps/server/tests/test_health_checks.py`

- [ ] Add candidate backlog and failed-candidate checks.
- [ ] Add pending memory operation backlog checks.
- [ ] Add embedding coverage check for active `MemoryItem` rows.
- [ ] Add stale access-log and retrieval-feedback sync checks.
- [ ] Add dirty retrieval-index signal if exposed by the Rust binding.

### Ticket MR-006: Retryable Extraction Failure

**Problem:** LLM extraction failure logs that facts from the turn are lost.

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/consolidation.py`
- Modify as needed: `apps/server/src/anima_server/models/runtime_memory.py`
- Test: `apps/server/tests/test_memory_candidates.py`

- [ ] Capture source message IDs and failure reason when extraction fails.
- [ ] Represent failed extraction as retryable background work or a failed candidate batch.
- [ ] Add health visibility for failed extraction work.
- [ ] Verify retry limits and error reporting.

## Phase 2: Recall Quality

### Ticket MR-101: `search_long_memory` Tool

Use `docs/superpowers/plans/2026-05-03-long-memory-evidence-tool.md` as the detailed implementation plan.

- [ ] Add mode-driven wide evidence retrieval.
- [ ] Register `search_long_memory(query, mode)` as an agent tool.
- [ ] Update system prompt guidance.
- [ ] Test tool output and trace visibility.

### Ticket MR-102: Remove Pre-Turn Classifier

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Modify: `apps/server/src/anima_server/services/agent/memory_blocks.py`
- Delete: `apps/server/src/anima_server/services/agent/retrieval_intent.py`
- Update tests tied to wide evidence wiring.

- [ ] Remove `_run_wide_evidence_retrieval`.
- [ ] Remove `evidence_memories` prompt block.
- [ ] Remove classifier tests.
- [ ] Keep compaction/reranking logic for the tool.

### Ticket MR-103: Hybrid Search Backfill

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/embeddings.py`
- Test: `apps/server/tests/test_hybrid_search.py`

- [ ] Write a test where early merged results are filtered out but later valid results exist.
- [ ] Change result resolution to continue through merged candidates until the requested limit is filled.
- [ ] Preserve heat floor and tag filters.

### Ticket MR-104: Exact Conversation And Transcript Recall

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Modify: `apps/server/src/anima_server/services/agent/conversation_search.py`
- Modify: `apps/server/src/anima_server/services/agent/transcript_search.py`
- Modify: `apps/server/src/anima_server/services/agent/templates/system_prompt.md.j2`

- [ ] Make transcript search guidance explicit in the prompt.
- [ ] Add broader transcript search mode for older exact recall.
- [ ] Consider semantic search for active conversation history if runtime embeddings are available.
- [ ] Add tests for old exact facts found through transcript fallback.

### Ticket MR-105: Recall Regression Probes

**Files:**
- Create: `apps/server/tests/test_memory_recall_regressions.py`
- Modify as needed: eval routes or fixtures.

- [ ] Cover explicit save recall.
- [ ] Cover pending candidate fallback.
- [ ] Cover keyword recall after index invalidation.
- [ ] Cover semantic recall after embedding.
- [ ] Cover latest/count long-memory recall.
- [ ] Cover archived transcript recall.

### Ticket MR-106: Prompt Tool Choice Rules

**Files:**
- Modify: `apps/server/src/anima_server/services/agent/templates/system_prompt.md.j2`
- Test: prompt rendering tests if present.

- [ ] Add concise rules for visible memory versus recall tools.
- [ ] Avoid keyword laundry lists.
- [ ] Verify the prompt names available tools accurately.

## Phase 3: Structured Provenance And Event Memory

### Ticket MR-201: Provenance Design

**Files:**
- Create or modify: `docs/prds/memory/provenance-and-event-memory.md`
- Modify: `docs/architecture/memory/memory-system.md`

- [ ] Define source fields: thread, message, observed date, speaker, confidence, extraction source.
- [ ] Decide whether to extend `MemoryClaimEvidence` or add a new source evidence table.
- [ ] Define privacy and encryption expectations.
- [ ] Define migration/backfill strategy.

### Ticket MR-202: Provenance Models And Migration

**Files:**
- Modify: `apps/server/src/anima_server/models/agent_runtime.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Create: `apps/server/alembic/versions/<revision>_memory_provenance.py`
- Test: model and migration tests.

- [ ] Add model fields or tables from MR-201.
- [ ] Create SQLite-compatible Alembic migration.
- [ ] Verify upgrade on a test DB.

### Ticket MR-203: Provenance Write Path

**Files:**
- Modify: `candidate_ops.py`
- Modify: `consolidation.py`
- Modify: `soul_writer.py`
- Modify: `tools.py`

- [ ] Attach source metadata to explicit saves.
- [ ] Attach source metadata to LLM and regex extraction candidates.
- [ ] Preserve source metadata during promotion/supersession.

### Ticket MR-204: Metadata-Aware Retrieval

**Files:**
- Modify: `evidence_retrieval.py`
- Modify as needed: `memory_blocks.py`
- Modify as needed: `tools.py`

- [ ] Add retrieval paths that sort/filter by observed date and source.
- [ ] Use metadata for latest and temporal answers.
- [ ] Use evidence rows for count aggregation when possible.

### Ticket MR-205: Backfill Existing Memory

**Files:**
- Create migration/backfill helper under `apps/server/src/anima_server/services/agent/` or `scripts/`.
- Test: backfill idempotency tests.

- [ ] Backfill provenance from existing message IDs where available.
- [ ] Preserve eval raw chunk behavior until the new model can replace it.
- [ ] Make the backfill idempotent.

### Ticket MR-206: Docs Reconciliation

**Files:**
- Modify: `docs/architecture/memory/memory-system.md`
- Modify: `docs/architecture/agent/agent-runtime.md`
- Modify: `docs/prds/memory/README.md`

- [ ] Remove stale claims about recall behavior.
- [ ] Document final store boundaries.
- [ ] Document operational health signals.

## Final Verification

- [ ] `bun run test:server`
- [ ] `bun run lint`
- [ ] `bun run build`
- [ ] `bun run db:server:current`
- [ ] Manual smoke: auth, chat, memory save, recall, settings, `GET /health`
