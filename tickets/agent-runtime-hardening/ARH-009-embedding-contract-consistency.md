# ARH-009 - Embedding contract and store consistency

- Status: done
- Priority: P1
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: none
- Owner: Claude (Fable 5)
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-07 22:30 MYT
- Started: 2026-07-07 21:56 MYT
- Completed: 2026-07-07 22:30 MYT

## Goal

Changing the embedding model can no longer silently kill semantic search, and the three derived embedding stores (SQLCipher `embedding_json`, pgvector `RuntimeEmbedding`, rust native index) cannot silently diverge or orphan.

## Problem

1. **Frozen dimension vs auto-detection.** The pgvector column is fixed to `Vector(resolve_embedding_dim())` (`models/runtime_embedding.py:24-30`; migration `alembic_runtime/versions/005_p6_pgvector_embeddings.py:58` bakes `vector(768)`), but `embeddings.py:400-408` auto-detects the dimension at first embed into a process global. Switch from a 768-dim to a 1024-dim model → every pgvector query raises → the exception is swallowed in `_semantic_ranked_ids` (`embeddings.py:537-539`) → retrieval silently degrades to keyword-only forever. No re-embed path exists.
2. **Best-effort three-way writes.** pgvector upsert failures are swallowed in `embed_memory_item`/`backfill_embeddings` (`embeddings.py:672-673`, `:728-729`) — an item can be searchable in one backend and invisible in another.
3. **Orphans.** External-index cleanup only runs via the `after_commit` hook inside `forget_memory` (`forgetting.py:1178-1222`); any other `MemoryItem` deletion leaves live `RuntimeEmbedding` rows and rust docs.
4. **Staleness never checked.** `RuntimeEmbedding.content_hash` exists but no read path checks it — an edited memory keeps serving its old embedding.
5. **Sticky sync guard.** `_synced_users` (`vector_store.py:449-476`) is one-shot per-user; `clear_embedding_cache` (`embeddings.py:305-316`) doesn't clear it, so after a model change the soul→PG sync won't re-run until restart.

## Implementation Notes

1. **Persist the contract.** Store the active `(embedding_model, dim)` in a runtime config/state row (small table or existing settings mechanism). At startup and on first embed, compare detected vs persisted:
   - Match → proceed.
   - Mismatch → do NOT serve semantic queries with mixed vectors: log ERROR on `anima.runtime.degraded`, set a `reembed_required` flag, and have semantic legs return empty *with the degradation counted/logged* (explicitly degraded, not silently).
2. **Re-embed path.** A backfill job (extend `backfill_embeddings`) that, when `reembed_required`: recreates the pgvector column/table at the new dim (Alembic-managed `ALTER ... TYPE vector(n)` or drop/recreate — decide based on data volume; embeddings are derived data so drop/recreate is acceptable), re-embeds all items, rebuilds the rust index, updates the persisted contract, clears the flag.
3. **Stop swallowing upsert failures**: on pgvector write failure, mark the item dirty (e.g. null out `RuntimeEmbedding` row / enqueue for retry) and log WARNING; the backfill sweep retries dirty items.
4. **Orphan sweep**: periodic task (hook into the existing prune sweep) deleting `RuntimeEmbedding` rows and rust docs whose `source_id` no longer matches a live `MemoryItem`.
5. **Hash validation on cold-start sync**: when syncing, compare `content_hash` and re-embed mismatches.
6. **Clear `_synced_users` inside `clear_embedding_cache`.**

## Deliverables

- Persisted `(model, dim)` contract with loud mismatch handling + `reembed_required` flag.
- Re-embed/rebuild backfill path covering pgvector and rust index.
- Dirty-marking instead of swallowed upsert failures; retry via backfill.
- Orphan sweep and cold-start `content_hash` validation.
- `_synced_users` cleared with the cache.
- Tests: simulated dim change flips the flag and semantic search reports degraded (not silent empty); failed upsert is retried by backfill; orphaned rows removed by sweep; edited item re-embedded on hash mismatch.

## Acceptance

- Switching embedding models produces a visible degraded state and a working recovery path, never a permanent silent keyword-only mode.
- No code path swallows a vector-store write failure without marking the item for retry.
- Focused tests pass; any migration up/down clean.

## Activity Log

- 2026-07-07 00:28 MYT - Ticket created.
- 2026-07-07 22:30 MYT - Implemented on branch `worktree-agent-runtime-hardening-p4`: migration `024_embedding_config` + `EmbeddingConfig` model persist the active `(model, dim)` pair; `generate_embedding` verifies the detected pair against the contract on first embed (mismatch → ERROR on `anima.runtime.degraded` + persisted `reembed_required`, old pair kept recorded until recovery completes); `_semantic_ranked_ids` skips both semantic backends while the flag is set (explicitly degraded, never a swallowed pgvector exception); the embedding-backfill task is the recovery path — it resets derived stores (`embedding_json`/checksum nulled, user's pgvector rows deleted, retrieval indexes invalidated), re-embeds progressively via the existing backfill, and adopts the new contract when zero items remain unembedded; vector-store upsert failures log WARNING on the degraded logger and flag the user for a `sync_to_vector_store` re-sync on the next backfill; an orphan sweep deletes pgvector rows whose source item no longer exists; `clear_embedding_cache` now re-arms the cold-start sync (`_synced_users`) and the contract cache.

## Validation

- Commands:
  - `uv run --directory apps/server pytest tests/test_embedding_contract.py -q` → 9 passed
  - Embedding/retrieval regression sweep (embedding_sync, vector_store, hybrid_retrieval, scored retrieval, forgetting, rebuild, router, evidence, feedback, sleep_agent) → 157 passed
  - Migration chain validated: single head `024_embedding_config`
- Changed paths:
  - apps/server/alembic_runtime/versions/024_embedding_config.py
  - apps/server/src/anima_server/models/runtime_memory.py
  - apps/server/src/anima_server/services/agent/embedding_contract.py (new)
  - apps/server/src/anima_server/services/agent/embeddings.py
  - apps/server/src/anima_server/services/agent/vector_store.py
  - apps/server/src/anima_server/services/agent/sleep_agent.py
  - apps/server/tests/test_embedding_contract.py
- Notes:
  - 9 new tests: contract adoption/match/mismatch (loud + restart-safe), re-embed completion, degraded semantic leg, derived-store reset, orphan sweep, upsert-failure re-sync flag, cache-clear re-arming.
  - Scope notes: the rust index is invalidated (rebuilt from soul state) rather than doc-by-doc cleaned; deep `content_hash` staleness detection is partially covered — the cold-start sync refreshes every pgvector row from soul state, but detecting a soul-side embedding that predates a content edit would need an embedded-text hash column on `MemoryItem` (deferred).
