# Agent Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the silent-failure, cost, durability, and drift problems found in the 2026-07-07 four-track agent runtime review (turn path, memory/retrieval, background cognition, prompt/LLM clients) without changing product behavior.

**Architecture:** No new subsystems. Each ticket hardens an existing seam: the turn lifecycle in `service.py`/`runtime.py`, the promotion pipeline in `soul_writer.py`/`consolidation.py`, the retrieval stack in `embeddings.py`/`pgvec_store.py`, and the provider clients in `anthropic_client.py`/`llm.py`. The unifying principles: fail loudly instead of silently degrading, persist state that currently lives in process memory, never redo LLM work on unchanged inputs, and keep exactly one copy of critical logic.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (PG runtime DB + SQLCipher soul DB), Alembic (`apps/server/alembic_runtime/`), pgvector, httpx, Anthropic Messages API / OpenAI-compatible providers, pytest.

---

## Planning Inputs

Review findings (2026-07-07), all paths under `apps/server/src/anima_server/` unless noted. Line numbers are anchors as of commit `1f661721` — re-verify before editing.

- Turn path: `services/agent/service.py`, `runtime.py`, `executor.py`, `turn_coordinator.py`, `streaming.py`, `persistence.py`, `api/routes/threads.py`
- Memory/retrieval: `services/agent/embeddings.py`, `pgvec_store.py`, `vector_store.py`, `adaptive_retrieval.py`, `forgetting.py`, `models/runtime_embedding.py`
- Background cognition: `services/agent/consolidation.py`, `soul_writer.py`, `sleep_agent.py`, `sleep_tasks.py`, `inner_monologue.py`, `reflection.py`, `eager_consolidation.py`, `models/pending_memory_op.py`
- Prompt/LLM: `services/agent/system_prompt.py`, `prompt_budget.py`, `memory_blocks.py`, `compaction.py`, `anthropic_client.py`, `openai_compatible_client.py`, `llm.py`, `llm_json.py`, `model_capabilities.py`, `adapters/`

## Scope

In scope:

- Bug fixes for silently-broken paths (Anthropic compaction, stranded runs, embedding-dim mismatch, capability gating).
- Durability: commit-before-LLM extraction, persisted gates, retry caps with backoff, optimistic locking on soul blocks.
- Cost: Anthropic prompt caching with a stable prefix, dirty-checks for background cognition, tool-output replay caps.
- Latency: parallelized turn-context assembly, single-decrypt retrieval.
- Consolidation of drifted duplicate logic (approval resume, sleep orchestrators, stream pumps).
- Focused pytest coverage for every fix.

Out of scope:

- New product features or behavior changes visible in chat.
- Swapping to the official `anthropic` SDK (evaluated inside ARH-005; adopt only if it simplifies, not as a goal).
- Retrieval-quality algorithm redesign (fusion weights, feedback-loop learning) beyond the correctness fixes in ARH-012.
- Desktop/client changes.
- `service.py` full decomposition (only the seams needed by ARH-013).

## Phase Order and Rationale

| Phase | Tickets | Theme |
| --- | --- | --- |
| 1 | ARH-001..003 | Broken-today fixes: dead compaction, stranded runs, lost identity writes |
| 2 | ARH-004, ARH-005 | Reliability plumbing: retry hygiene, structured errors, capability gating |
| 3 | ARH-006..008 | Cost: prompt caching, dirty-checks, context/token hygiene |
| 4 | ARH-009, ARH-010 | Durability: embedding contract, extraction crash-safety |
| 5 | ARH-011, ARH-012 | Latency + retrieval scoring correctness |
| 6 | ARH-013 | Structural dedup of drifted copies |

Phases 1–2 land first because later phases build on their primitives (structured error codes from ARH-005 feed ARH-004 backoff; persisted task-run gates from ARH-004 feed ARH-007 dirty-checks).

## Cross-Cutting Decisions

- **New columns/tables go through Alembic** in `apps/server/alembic_runtime/versions/` (next free number after 021). Needed by: ARH-004 (`PendingMemoryOp.retry_count`, `RuntimeThread` archival retry state), ARH-007 (contradiction verdict cache), ARH-009 (embedding config record), ARH-013 (consolidation cursor table, if extracted).
- **Structured LLM errors:** `LLMInvocationError` gains `status_code: int | None` and `retry_after: float | None` attributes, set in `wrap_llm_error` (`llm.py:252`). All retryability checks switch from message-substring matching to the integer code. Retryable set: 408, 429, 500, 502, 503, 504, 529.
- **One shared retry helper:** `invoke_with_retry(client, request, *, config)` extracted from `AgentRuntime._invoke_llm_with_retry` (`runtime.py:1306`) into `llm.py`, used by the runtime, `call_llm_for_text` (`llm_json.py:45`), and compaction.
- **Persisted gates use `RuntimeBackgroundTaskRun`:** any "last time X ran" check derives from the newest completed run row for that `task_type`, never from process-memory dicts.
- **Fail loudly:** replaced swallow-points log at WARNING minimum with a distinct logger name (`anima.runtime.degraded`) so degradation is greppable.

## Tasks

### Phase 1 — Broken today

- [ ] **ARH-001** Route `summarize_with_llm` (`compaction.py:345`) through `create_provider_chat_client`/`call_llm_for_text` instead of raw httpx POST to `/chat/completions`; raise the summarization failure to WARNING; add a test that asserts the Anthropic provider path produces an LLM summary (mock client) and that fallback is logged when it fails.
- [ ] **ARH-002** Add `asyncio.CancelledError` handling to turn setup/emit/persist stages in `service.py` (sites near `:711`, `:725`, `:1079`, `:826`) mirroring the Stage-2 handler at `:754`; make the stream-worker sentinel `put_nowait` (`service.py:2760`, `:573`); route fire-and-forget `create_task` sites (`api/routes/threads.py:105`, `:162`; `service.py:918`, `:2825`) through `_track_background_task`. Test: cancel a turn during context assembly and assert the run row is not left `running`.
- [ ] **ARH-003** Optimistic locking for soul blocks: `_write_soul_block` (`soul_blocks.py:26`) accepts `expected_version`; deep monologue (`inner_monologue.py:633→806`), quick reflection (`:187`), and intentions rebuild (`:886`) capture version at read and re-read/re-apply on conflict. Also replace (not append) the `## Learned Rules` section via `intentions.py` helpers. Test: concurrent pending-op append + monologue write; the append survives.

### Phase 2 — Reliability plumbing

- [ ] **ARH-004** Retry hygiene: Alembic migration adding `PendingMemoryOp.retry_count` (skip at cap 3, mirror `candidate_ops.py` savepoint pattern for the Phase-2 IntegrityError fallback at `soul_writer.py:329`); archival retry state + exponential backoff for `inactivity_sweep` (`eager_consolidation.py:180`); deep-monologue 24h gate derived from `RuntimeBackgroundTaskRun` instead of `_last_deep_monologue` (`sleep_tasks.py:437`).
- [ ] **ARH-005** LLM client robustness: structured `status_code`/`retry_after` on `LLMInvocationError`; integer-based retry classification replacing substring checks (`runtime.py:87`); shared `invoke_with_retry` wrapping `call_llm_for_text`; vision patterns covering current models (generic `claude-` match, `model_capabilities.py:12`); drop/gate `temperature` for Anthropic models that reject it (`anthropic_client.py:164`); surface `stop_reason` and flag `max_tokens`/`refusal` (`anthropic_client.py:388`); pass `strict` through Anthropic tool serialization (`anthropic_client.py:340`).

### Phase 3 — Cost

- [ ] **ARH-006** Prompt caching: split `system` into ordered blocks — stable prefix (rules, guardrails, persona, static tier-0 memory blocks) with `cache_control: {type: ephemeral}` on the last stable block; volatile content (timestamp rounded to minute, retrieved memories, mood/relationship state) after the breakpoint or in the latest user turn. Touch `system_prompt.py:46`, templates, `memory_blocks.py` block ordering, `anthropic_client.py:158`. Verify with a two-turn integration test asserting byte-identical prefix.
- [ ] **ARH-007** Background dirty-checks: persist contradiction pair verdicts (new table or item metadata) keyed on content hashes, skip unchanged pairs (`sleep_tasks.py:267`); drop `force=True` heat-bypass for the contradiction scan (`reflection.py:149`); per-task input-freshness check (max `created_at` of inputs vs last completed run) before pattern/profile synthesis (`sleep_agent.py:178`); gate soul-writer Phase 4 emotional-pattern promotion on elapsed time or new-signal count (`soul_writer.py:426`) with the deep monologue as single call site.
- [ ] **ARH-008** Context/token hygiene: cap the in-history/persisted tool-return form at 8k chars while keeping the full output in the step trace (`executor.py:359`, `runtime.py:564`, `persistence.py:354`); slim per-step `request_json` snapshots to previews/deltas (`persistence.py:574`); replace raw `value[:2000]` slices in block builders with `_truncate_lines` (`memory_blocks.py:450` et al.); calibrate the chars/4 estimate (use `count_tokens` where available, else chars/3 + fixed scaffolding reservation, `prompt_budget.py:147`, `:106`).

### Phase 4 — Durability

- [ ] **ARH-009** Embedding contract: persist active `(model, dim)` in a runtime config row; on mismatch at startup or first embed, refuse semantic search loudly and mark a re-embed backfill needed instead of swallowing in `_semantic_ranked_ids` (`embeddings.py:537`); make pgvector upsert failure mark the item dirty for retry instead of pass (`embeddings.py:672`, `:728`); orphan sweep for `RuntimeEmbedding`/rust-index docs whose `source_id` no longer exists; validate `content_hash` on cold-start sync; clear `_synced_users` in `clear_embedding_cache` (`vector_store.py:449`, `embeddings.py:305`).
- [ ] **ARH-010** Extraction durability: in `run_background_extraction` (`consolidation.py:389-599`) commit regex-derived candidates and a `MemoryExtractionFailure` intent row *before* the LLM call, resolve intent on success; don't hold the PG session across the LLM await; catch `CancelledError` distinctly so shutdown doesn't drop the turn. Test: kill the task between commit and LLM; soul-writer retry recovers the candidates.

### Phase 5 — Latency and scoring correctness

- [ ] **ARH-011** TTFT: `asyncio.gather` soul-writer promotion + `hybrid_search` in `_assemble_turn_context` (`service.py:1152-1361`); move feedback-signal correction (`feedback_signals.py:450`) to `_track_background_task`; single-decrypt retrieval — decrypt each surviving item once and thread plaintext through rerank and fragment building, deleting `_bm25_rerank`'s second BM25 pass (`embeddings.py:929-931`, `:1185`, `service.py:1221`).
- [ ] **ARH-012** Retrieval scoring correctness: apply `absolute_min` against pre-normalization top score in `find_adaptive_cutoff` (`adaptive_retrieval.py:218`); treat genuine `heat == 0.0` as below-floor and distinguish unscored NULL (`embeddings.py:628`, `:1177`); define one normalized similarity contract across the rust index and pgvector backends with a parity test (`embeddings.py:548`, `pgvec_store.py:138`); push the heat/visibility filter into pgvector SQL or expand candidates until `limit` valid results (`pgvec_store.py:136`).
- [ ] **ARH-013** Dedup drifted logic: extract `_process_step_tool_calls` shared by `invoke` and `resume_after_approval` (`runtime.py:434-675` vs `:756-1133`), restoring deferred-call handling / failure exclusion / memory refresh on the resume path — and thread `extra_tool_schemas` + `conversation_turn_count` through the mid-turn memory refresh rebuild (`runtime.py:632`); make `/sleep` call `run_sleeptime_agents(force=True)` and delete `run_sleep_tasks` (`sleep_tasks.py:77`, `api/routes/chat.py:571`); collapse the two stream pumps into one `_stream_via_queue` helper (`service.py:540` vs `:2715`); delete the ignored `initial_sequence_id` params (`service.py:2361`, `:2280`); move the consolidation cursor out of task-run `result_json` scanning into a dedicated cursor table with a retention sweep (`sleep_agent.py:736`).

## Validation

Per ticket: focused pytest under `apps/server/tests/`, plus the repo's standard checks. Final: one full agent-turn smoke (send a message through the API, confirm streaming, memory extraction, and background consolidation complete) and grep for `anima.runtime.degraded` in logs to confirm no silent-failure regressions.

## Source

- Review date: 2026-07-07 (four parallel subsystem reviews)
- Tickets: `tickets/agent-runtime-hardening/`
- Parent tracker: `tickets/agent-runtime-hardening/ARH-000-parent.md`
