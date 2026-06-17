# Agent Server Audit — 2026-06-11

Four parallel audits of `apps/server/src/anima_server/services/agent/` (~33k lines, 77 modules):
correctness/reliability, response quality, performance/latency, architecture. All findings were
verified against the actual code paths by the auditing agents (file:line refs checked, guards
elsewhere accounted for).

---

## 1. Correctness & Reliability

### High

- **C-H1 — Mid-turn cancellation is dead end-to-end.** The run row is only flushed, never
  committed, before the LLM call (`persistence.py:152-171`; first commit at `service.py:1557`),
  so `POST /api/chat/runs/{run_id}/cancel` (`chat.py:607`) 404s for any in-flight run from its
  fresh session. No stream event carries `runId` during a normal turn (`streaming.py`), and the
  WS `cancel` handler is a `pass` stub (`ws.py:227-228`). Fix: commit the run row in a short
  transaction after `create_run`, emit `run_started` with `runId`, implement the WS handler.
- **C-H2 — Stage-1 failure leaves a zombie "running" run + orphaned in-context user message.**
  Unguarded code after run/user-message persistence (`service.py:969,974,618`); the stream
  worker swallows the exception (`service.py:1716-1717`), SSE completes "successfully", and DB
  teardown commits (`db/runtime.py:108`) — run stuck `running` forever, user message replays as
  unanswered history next turn. Only Stage-2 failures get cleanup (`service.py:1253-1272`).
  Fix: extend mark-failed + context-eviction to Stages 1/1b.
- **C-H3 — Context-overflow retry double-executes tools.** On `ContextWindowOverflowError` the
  whole `runner.invoke` re-runs (`service.py:1208-1252`, on by default via
  `agent_context_overflow_retry`); tools already executed in earlier steps (`create_task`,
  `save_to_memory`, client `bash`, …) run again — no idempotency tracking. Fix: only retry if
  failure happened before any tool execution, or carry executed-call IDs into the retry.

### Medium

- **C-M1 — Tool timeout doesn't stop sync tools** (`executor.py:142-158`) — thread keeps running,
  side effects land, and `_decide_continuation` (`runtime.py:1148-1154`) invites a re-run →
  duplicate side effects.
- **C-M2 — Approval resume produces duplicate `tool_result` for the same call id** → Anthropic 400
  after the approved tool already ran (`persistence.py:415-425`, `messages.py:60-61`,
  `runtime.py:824-830`, `anthropic_client.py:194-216`). Fix: evict the approval-required
  tool-error message in `clear_approval_checkpoint`.
- **C-M3 — Approval is one-shot non-resumable**: checkpoint cleared before resume
  (`service.py:332-333`); if the follow-up LLM call fails, approved side effects are lost and
  re-approval impossible. Fix: clear checkpoint in the same commit as successful persistence.
- **C-M4 — WS approval protocol is dead** (`ws.py:223-224` stub; `ws.py:284-285` drops
  `approval_pending`/`cancelled`). (Note: latent — see C-L1.)
- **C-M5 — PG row lock held across LLM calls.** `reserve_message_sequences` takes
  `SELECT ... FOR UPDATE` on the thread row (`sequencing.py:20-24`) inside the turn transaction
  that stays open for minutes; `/chat/reset`, `create_thread`, background `append_message` block.
  Fix: allocate sequences in a short standalone transaction.
- **C-M6 — Blocking sync `httpx.get` on the event loop per delegated turn** in `_load_mod_tools`
  (`tools.py:1310`), failure uncached — anima-mod down freezes the server up to 5s per turn.
- **C-M7 — No overall timeout on streaming LLM calls** (only the non-streaming branch is wrapped,
  `runtime.py:1266-1270`); a stalled stream pins the thread lock + open transaction ≥600s.
  Fix: inactivity timeout around stream consumption.

### Low

- **C-L1** — `RequiresApprovalToolRule` never instantiated (`rules.py:28`, `rules.py:270-275`);
  the ~500-LOC approval pipeline is unreachable in production config.
- **C-L2** — `mark_run_failed` overwrites terminal status (`persistence.py:315-319`).
- **C-L3** — Silent `except Exception: pass` at `service.py:1030-1031` (feedback signals) and
  `service.py:1582-1583` (LLM compaction) — add logging.
- **C-L4** — `_cancel_events` map leaks (`companion.py:208-213`).
- **C-L5** — Lock-LRU eviction race in `turn_coordinator.py:39-43` (needs >512 interleaved threads).
- **C-L6** — httpx clients leak on cache invalidation (`llm.py:89-90`;
  `openai_compatible_client.py:92-94`).
- **C-L7** — Anthropic streaming drops `thinking_delta`/`signature_delta`
  (`anthropic_client.py:534-560`); adapter overwrites usage with last chunk →
  `prompt_tokens=None` (`adapters/openai_compatible.py:170-172`).
- **C-L8** — Dead `initial_sequence_id` param (`service.py:1453`).
- **C-L9** — Raw exception text reaches clients (`service.py:1717`, `ws.py:205-213`).
- **C-L10** — Cosmetic: duplicate `_background_tasks` (`consolidation.py:30,33`); done-callback
  bypasses lock (`consolidation.py:643`); deprecated `get_user_lock` shim; executor
  `memory_modified` mis-attribution (`executor.py:249-261`).

Verified sound: retry double-streaming guard, sequential `execute_parallel` (intentional),
orphaned-tool-message sanitization, window trimming at tool-call boundaries, bounded stream
queues, background-task references, sequence reservation under the per-thread lock.

---

## 2. Response Quality (retrieval / context assembly / consolidation)

### High

- **Q-H1 — Prompt budget decoupled from the model's context window.** `BudgetConfig` is a
  hard-coded 24k-char budget (`prompt_budget.py:86-93`) independent of `agent_max_tokens` /
  provider window; compaction triggers on a different budget. Small-context models get starved
  conversation; large-context models get needlessly dropped tier-3 blocks. Fix: derive
  `total_budget` from the resolved context window minus output reserve, tool schemas, and a
  conversation floor.
- **Q-H2 — Automatic per-turn retrieval has no recency/importance in fusion.** Live path
  (`service.py:889-918` → `hybrid_search`, `embeddings.py:972-1125`) is pure semantic+BM25 RRF
  at 0.5/0.5. The date/intent-aware reranker (`evidence_retrieval.py:633-741`) only runs via the
  `search_long_memory` tool. Fix: blend recency + heat into the automatic path post-hybrid-search.
- **Q-H3 — No cross-block dedup**: `relevant_memories` (`memory_blocks.py:139-143,205-231`)
  re-renders the same items as facts/preferences/goals blocks (`memory_blocks.py:234-311`),
  wasting budget and duplicating facts in the prompt. Fix: exclusion set across block builders.
- **Q-H4 — Claim dedup keys freeform facts on a lossy 60-char slug** (`claims.py:76-84`);
  paraphrases create parallel "active" claims that never supersede each other. Fix: embedding-
  similarity resolution for the no-slot fallback before inserting.

### Medium

- **Q-M1 — Mid-sentence character-slice truncation** (`prompt_budget.py:181,237-240`) — half
  facts like "user is allergic to". Fix: boundary-aware truncator (reuse
  `evidence_retrieval._truncate`).
- **Q-M2 — Compaction fallback keeps first-12-positional lines** (`compaction.py:135,201-227`),
  no salience selection; LLM cascade falls back to this on any failure (`compaction.py:455-459`).
- **Q-M3 — Episodes have no provenance** — no source-message ids persisted (episodes build path;
  rendered at `memory_blocks.py:466-491`); drifted summaries are unverifiable.
- **Q-M4 — Heat formula: recency multiplies all terms** (`heat_scoring.py:101-103`); after ~3
  idle days even importance-5 items fall below `HEAT_VISIBILITY_FLOOR` (`forgetting.py:33`) and
  stop being retrieved ("I'm diabetic" goes invisible). Fix: importance as additive floor outside
  the recency multiplier.
- **Q-M5 — Retrieval feedback never adjusts system parameters** — only per-item importance ±1
  and heat (`retrieval_feedback.py:550-569,309-373`); fusion weights/thresholds are constants
  (`embeddings.py:1017-1018`). Fix: per-user EMA on fusion weights from was_used by leg.

### Low

- **Q-L1** — Adaptive cutoff runs on post-rerank rescaled scores (`embeddings.py:1121-1123` then
  `service.py:901`) — distribution it wasn't tuned for.
- **Q-L2** — `touch_memory_items` counts "rendered" as "accessed"
  (`memory_blocks.py:247,273,299,380`), pumping heat for displayed-but-unused items.
- **Q-L3** — BM25 fallback: full rebuild per add (`bm25_index.py:62-65`); unbounded decrypted-
  plaintext module cache (`bm25_index.py:131-158`).

---

## 3. Performance & Latency

### High

- **P-1 — All DB on sync SQLAlchemy sessions inside async code** (`chat.py:69-70`, `ws.py:193-194`,
  whole `service.py` pipeline) — every SQLCipher/PG query blocks the event loop; concurrent
  streams serialize behind each other. Fix: async sessions, or `asyncio.to_thread` around the
  three sync-heavy phases.
- **P-2 — Pre-turn Soul Writer awaited inline with per-candidate LLM calls** (`service.py:860-874`
  → `soul_writer.py:424-431`) — TTFT grows O(candidates × LLM latency). Fix: pre-turn do only
  fast op promotion; background the LLM candidate promotion (pending block already surfaces
  unpromoted candidates).
- **P-3 — KG block can freeze the loop on `thread.join()` around an embedding HTTP call**
  (`knowledge_graph.py:197-230` via `memory_blocks.py:994-1032`) whenever no entity substring-
  matches. Also recompute: `query_embedding` from `service.py:899` isn't passed in. Fix: thread
  the existing embedding through; delete `_run_async_blocking` from this path.
- **P-4 — Memory blocks fully rebuilt every turn** (`service.py:973-995`): ~20 builders, ~80 item
  decrypts, KG traversal, per turn; the companion static-block cache only serves failed-retrieval
  turns (`companion.py:275-294`). Fix: split static vs query-aware builders; cache static via the
  existing version counter.
- **P-5 — `done` event waits on an LLM summarization call** (`service.py:1576-1587` →
  `compaction.py:373`) while holding the thread lock. Fix: emit `done` post-commit, background
  the LLM compaction.

### Medium

- **P-6** — Retrieval legs serial though independent (`embeddings.py:1052-1079`): embedding →
  semantic → BM25. Gather embedding+semantic with BM25; overlap static block loads.
- **P-7** — BM25 fallback rebuild O(corpus × decrypt) inline in `hybrid_search`
  (`bm25_index.py:104-158`); `invalidate_index` fires per vector upsert
  (`vector_store.py:339-343`). Debounce + thread.
- **P-8** — ~80 `MemoryAccessLog` inserts/turn from `touch_memory_items`
  (`memory_store.py:673-694`). Batch to one insert per turn.
- **P-9** — Items decrypted 2–3× per turn (BM25 build, rerank, semantic loop, block builders).
  Per-turn plaintext cache keyed by item id.
- **P-10** — Pure-Python cosine over ~240-item pools per turn (`memory_store.py:585-637`,
  `embeddings.py:449-454`). Vectorize or restrict to retrieval top-k.
- **P-11** — New `httpx.AsyncClient` per embedding request (`embeddings.py:406,427`). Share one.
- **P-12** — KG entity table fully scanned + Python cosine per turn (`knowledge_graph.py:684,
  262-277`). SQL match / pgvector entity embeddings.
- **P-13** — N+1 in `build_pending_ops_block` (`memory_blocks.py:744-752`).

### Low

- **P-14** — Per-turn bookkeeping queries (candidate counts, 4× `get_pending_ops`,
  `count_messages_by_role` — verify `(thread_id, role)` index).
- **P-15** — Sync-only tools called directly on the loop (`executor.py:218-221` edge case).
- **P-16** — Background consolidation steals loop time via sync DB (resolved by P-1).

Confirmed good: shared async chat clients, SQL-side pgvector ANN, post-turn extraction genuinely
backgrounded.

---

## 4. Architecture

Verdict: the runtime/service seam is healthy — don't merge or re-split. Real debt: the
`AnimaCompanion` god object, two import-cycle clusters held together by deferred imports, ~600
lines of LLM/session boilerplate, ~740 lines of dead code.

| # | Priority | Recommendation | Payoff |
|---|---|---|---|
| A-1 | High | `call_llm_for_json(system, prompt, response_type, client=None)` helper next to `llm.py`; migrate consolidation, knowledge_graph, inner_monologue, sleep_tasks, episodes, batch_segmenter | Removes ~120–180 duplicated lines AND creates the injection seam that makes the four under-tested 1k-line modules testable |
| A-2 | High | Delete dead code: `predict_calibrate.py` (417 lines, zero importers), `streaming_utils.py` (220 lines, superseded by `streaming.py`), `consolidation.consolidate_pending_ops()` (deprecated, uncalled) + their tests | ~740 lines of false signal gone |
| A-3 | High | Split `AnimaCompanion` into `CompanionMemoryCache` / `ConversationWindowCache` / `CancellationRegistry` behind the existing facade | Defuses the only true god object (mutated from 15+ service sites, deferred-imported from 11 tools.py functions); enables P-4 |
| A-4 | High | Move shared types into `runtime_types.py` to break `anthropic_client → attachments → state → prompt_budget → memory_blocks` | Dissolves the 16-module import cluster; transport stops depending on memory domain |
| A-5 | Med | Extract embedding generation from `embeddings.py` (1,303 lines, fan-in 10) into `embedding_provider.py`; remove factory logic from the `vector_store.py` ABC | Layers the central chokepoint; interface stops importing implementations |
| A-6 | Med | `db_helpers.py` with `session_scope()` / `dual_session_scope()`; migrate 8+ hand-rolled sites | Copy-pasted dual-DB commit ordering is a latent consistency bug |
| A-7 | Med | Direct tests for `proactive.py`, `sleep_tasks.py` (cheap after A-1), then `inner_monologue.py` (1,091 lines, 1 test file), `self_model.py` (1,133 lines, 3) | Closes the biggest live-path coverage gaps |
| A-8 | Low | Extract `TurnContextBuilder` from `service.py:746-1049` | service.py < 1,500 lines; retrieval stage unit-testable |
| A-9 | Low | `provider_common.py` for shared client helpers; route `openai_compatible_client.py:436`, `rules.py:190` through `json_utils` | ~80 lines deduped; robust LLM-output parsing |
| — | Skip | Unifying SSE streaming across clients; splitting runtime.py helpers; re-cutting runtime/service | Differences legitimate; cohesion fine |

Also: `memory_refresher` closure (service.py:1162-1177 → runtime.py:618-642) is a hidden
back-channel; pass cache handles through `ToolContext` after A-3.

False orphans confirmed live: `batch_segmenter.py`, `proactive.py`,
consolidation vs eager_consolidation (different jobs), sleep_agent vs sleep_tasks (gate vs exec).

---

## Synthesis — recommended order of attack

Cross-cutting observations:
- `touch_memory_items` shows up three times (P-8 write amplification, Q-L2 heat pollution,
  per-turn loop work) — one fix addresses all three.
- BM25 fallback shows up twice (P-7, Q-L3) — debounced threaded rebuild + LRU plaintext cache.
- The "agent feels forgetful/stale" symptom is the compound of Q-H1 + Q-H2 + Q-M4.
- P-1 (sync DB) multiplies the value of every other latency fix under concurrency.

### Phase 1 — Quick wins (small, localized, high payoff)
1. C-H2 zombie-run cleanup for Stage-1 failures.
2. C-H3 guard overflow retry against double tool execution.
3. P-2 defer Soul Writer LLM work off the pre-turn path.
4. P-3 pass existing query embedding to the KG block; remove `thread.join`.
5. P-5 emit `done` before LLM compaction.
6. A-2 delete dead code.
7. C-L3 add logging to silent excepts; C-L9 mask raw exception text.

### Phase 2 — Response quality
8. Q-H1 tie prompt budget to the resolved context window.
9. Q-H2 recency+heat blend in automatic retrieval.
10. Q-H3 cross-block dedup.
11. Q-M4 importance floor in heat formula.
12. Q-M1 boundary-aware truncation.
13. Q-H4 embedding-similarity claim dedup.

### Phase 3 — Structural
14. P-1 async DB (or to_thread the three sync-heavy phases).
15. A-1 `call_llm_for_json` + test seams; A-7 tests for the big untested modules.
16. A-3 companion split, then P-4 static/query-aware block caching.
17. C-H1 make cancellation work end-to-end.
18. C-M5/C-M7 transaction scope + stream inactivity timeout.
19. A-4/A-5/A-6 import-cycle and session-scope cleanups.

### Status (2026-06-12)
- Phase 1: DONE (all 7 items).
- Phase 2: DONE (all 6 items).
- Phase 3: DONE — A-1 (`llm_json.py` helpers, 6 modules migrated), C-H1
  (early run commit + `run_started` event + WS cancel handler + race-safe
  cancel events), C-M5 (early commit releases the thread-row lock), C-M7
  (`agent_llm_stream_inactivity_timeout`, cancel-aware stream wait), C-M6
  (mod-tools background fetch + negative cache), P-4 (static identity
  blocks cached via version counter, volatile/query blocks per turn;
  Soul Writer now invalidates the cache).
### Self-review pass (2026-06-12)
An adversarial review of the Phase 1-3 diff caught three real regressions,
now fixed + tested:
- **Early-commit zombie (Stage 3)**: the early run commit (C-H1/C-M5) meant a
  failure in `_persist_turn_result` left the run "running" and the user
  message replaying as history — Stage 3 had no failure handler. Fixed:
  Stage 3 now routes through `_fail_turn_setup`, which was hardened to
  roll back first and act only on a still-"running" run (safe to call from
  any path). Regression test added.
- **Stream-timeout generator leak (C-M7)**: on timeout/cancel the async
  generator was `aclose()`d while its `anext` task was still suspended,
  raising "async generator already running" (swallowed) and leaking the
  HTTP stream. Fixed: the cancelled task is drained before `aclose()`.
- **Claim-dedup nondeterminism (Q-H4)**: `_find_similar_freeform_claim`
  early-returned on the first classifier "update"/"duplicate" from an
  unordered query, bypassing the similarity threshold and picking a
  storage-order-dependent target. Fixed: deterministic ordering + ranked
  best-candidate scan.
Confirmed sound by the review: background compaction (no deadlock),
stream chunk-race (yields the chunk, no loss), mod-tools threading, heat
importance floor.

- REMAINING (deliberately deferred): P-1 async-DB migration (requires
  re-architecting session ownership across the turn pipeline — a
  half-measure with to_thread + shared sync sessions would be unsafe);
  A-3 companion class split (cosmetic once P-4 landed); A-4/A-5/A-6
  import-cycle and session-scope cleanups; A-7 direct tests for
  inner_monologue.py and self_model.py (the A-1 injection seam now makes
  these cheap to write).

Approval-flow findings (C-M2/M3/M4) are latent until `RequiresApprovalToolRule` is actually used
(C-L1) — fix them when/if that feature ships.
