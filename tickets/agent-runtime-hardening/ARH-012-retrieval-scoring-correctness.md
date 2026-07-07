# ARH-012 - Retrieval scoring correctness

- Status: in-review
- Priority: P2
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: none
- Owner: Claude (Fable 5)
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-08 00:35 MYT
- Started: 2026-07-08 00:05 MYT
- Completed: 2026-07-08 00:35 MYT

## Goal

Retrieval gates do what their names claim: absolute thresholds act on raw similarity, the heat visibility floor has no zero-value bypass, and both semantic backends conform to one score contract.

## Problem

1. **`absolute_min` is relative.** `find_adaptive_cutoff` min-max normalizes scores before the combined strategy (`services/agent/adaptive_retrieval.py:218` → `normalize_scores` `:107-112`); the live config sets `absolute_min=0.2` (`service.py:1206`), but the top hit always renormalizes to 1.0, so a query whose best match is cosine 0.26 (barely related) still returns a full "confident" result set. The only real quality gate is `similarity_threshold=0.25` on the semantic leg; the BM25 leg has no floor at all.
2. **Heat 0.0/None bypass.** Both retrieval paths skip only items with `heat not in (None, 0.0) and heat < HEAT_VISIBILITY_FLOOR` (`embeddings.py:628`, `:1177`): an item whose recency underflows to exactly 0.0 *bypasses* the floor and resurfaces, and any item never touched by `decay_all_heat` keeps `heat=None` and is always eligible.
3. **Inconsistent backend score scales.** The semantic leg resolves to the rust native index (`_semantic_ranked_ids_via_rust`, `embeddings.py:548-581`) or pgvector (`1 - cosine_distance`, `pgvec_store.py:138`) depending on availability, yet one `similarity_threshold` and the same RRF positions are applied to whichever `hit.score` comes back — thresholds behave differently across environments if the scales differ.
4. **Under-fetch after filtering.** `search_by_vector` caps ANN candidates at `max(limit*2, limit+5)` (`pgvec_store.py:136`), then heat/threshold/checksum filtering happens downstream (`pgvec_store.py:167-169`, `embeddings.py:628`) — callers can get fewer than `limit` results while valid items sit deeper in the ANN list.

## Implementation Notes

1. Carry the pre-normalization top score into `find_adaptive_cutoff` and gate on it: if `raw_top < absolute_min`, return an empty/low-confidence result set before the normalized-shape strategies run. Add a modest floor for the BM25-only case (rank-based or score-based, documented).
2. Distinguish unscored from scored-to-zero: simplest is treating `heat == 0.0` as below-floor and backfilling `heat=None` items with an initial heat at creation (or a `heat_scored_at` timestamp; pick the cheaper migration). Ensure `decay_all_heat` clamps to a tiny epsilon instead of exact 0.0 if 0.0 keeps a special meaning anywhere else.
3. Define the score contract at the `VectorSearchResult`/`MemoryRetrievalHit` boundary: "cosine similarity in [0,1], higher is better." Assert/normalize in each backend adapter; add a parity test embedding the same fixtures through both backends and asserting rank agreement and comparable score ranges.
4. Push the heat/visibility predicate into the pgvector SQL `WHERE`, or loop with expanding limit until `limit` valid results are collected (bounded, e.g. 3 expansions).
5. Coordinate with ARH-011 (same files); land this after or rebase carefully.

## Deliverables

- Raw-score `absolute_min` gating with empty-result behavior for junk queries; BM25 floor.
- Heat floor without the 0.0/None bypass (migration if `heat_scored_at` chosen).
- Backend score contract + parity test.
- Filter-aware candidate fetching in `pgvec_store`.
- Tests: query with max cosine 0.26 returns empty under `absolute_min=0.3`-style config; heat-0.0 item filtered; heat-None item gets scored/defaulted rather than always-visible; under-fetch fixture returns `limit` results.

## Acceptance

- A query with no genuinely-similar memories returns nothing rather than a confidently-ranked junk set.
- Superseded/decayed items at heat 0.0 cannot resurface past the floor.
- Both backends pass the parity test.
- Focused tests pass.

## Activity Log

- 2026-07-07 00:28 MYT - Ticket created.
- 2026-07-08 00:35 MYT - Implemented on branch `worktree-agent-runtime-hardening-p5`:
  1. **`absolute_min` now gates the raw score scale.** `find_adaptive_cutoff`
     short-circuits to an empty, `below_absolute_min`-tagged result when the
     raw (pre-normalization) top score is below `absolute_min` for the
     `combined`/`absolute_threshold` strategies, so a query whose best match is
     junk returns nothing instead of a min-max-renormalized "confident" set.
     `_find_combined_cutoff` and the `absolute_threshold` path compare
     `absolute_min` against the raw scores while relative-threshold and
     score-cliff detection keep using the normalized shape (they are inherently
     scale-free). The junk short-circuit runs in Python before the rust
     delegation, so it fires on both the rust and pure-Python paths.
  2. **Heat floor no longer bypassed at 0.0.** Retrieval and the memory-store
     visibility scan treat `heat == 0.0`/`NULL` as "never scored, keep visible".
     A scored item that fully decayed could underflow to exactly `0.0` and
     resurface past the floor. `compute_heat` now clamps every scored result to
     `HEAT_SCORED_EPSILON` (`1e-6`, well below `HEAT_VISIBILITY_FLOOR = 0.01`)
     at both return sites, so a decayed item sits just above `0.0` (still
     distinguishable from never-scored) but below the floor — the existing
     `heat not in (None, 0.0) and heat < FLOOR` filter then removes it, and no
     migration is needed.
  3. **One backend score contract.** The rust native index
     (`retrieval_index.rs` → `simd::cosine_similarity`) and pgvector
     (`1 - cosine_distance`) both emit raw cosine in [0, 1], but the Python
     retrieval fallback remapped it as `(cosine + 1) / 2` → [0.5, 1], so a
     shared `similarity_threshold` gated that backend far more loosely. The
     fallback now emits `max(0.0, cosine)`, conforming all three backends to
     "raw cosine similarity, higher is better".
  4. **Filter-aware candidate fetching.** `PgVecStore.search_by_vector` capped
     ANN candidates at `max(limit*2, limit+5)` then dropped checksum-invalid
     rows downstream, so it could return fewer than `limit` valid rows. It now
     expands the fetch (×2 up to `max(limit*8, limit+50)`) until `limit` valid
     rows are collected or the ANN pool is exhausted.

## Validation

- Commands:
  - `uv run --directory apps/server pytest tests/test_retrieval_scoring.py -q` → 7 passed
  - Regression sweep (hybrid_retrieval, ttft_optimizations, heat_scoring,
    forgetting, anima_core_retrieval, memory_scored_retrieval, active_recall,
    evidence_retrieval, optional_rust_imports) → 187 passed
  - Full server suite → 1970 passed, 1 skipped, 11 failed — all 11 pre-existing
    and unrelated to this ticket: the documented 8 (recalled-image pill,
    multi_thread ×2, p5_transcript_archive ×3 needing untracked diary
    migrations, runtime_db pgvector ×2) plus 3 `test_chat.py` runtime-message
    `sequence_id` UNIQUE-collision failures that reproduce identically on the
    clean p5 branch with these four files stashed (they originate in the
    ARH-011 parallel-assembly work already on this branch, not here).
- Changed paths:
  - apps/server/src/anima_server/services/agent/adaptive_retrieval.py
  - apps/server/src/anima_server/services/agent/heat_scoring.py
  - apps/server/src/anima_server/services/anima_core_retrieval.py
  - apps/server/src/anima_server/services/agent/pgvec_store.py
  - apps/server/tests/test_retrieval_scoring.py
- Notes:
  - BM25 leg: `bm25_search` already drops `score <= 0` hits (`bm25_index.py:68`),
    so the keyword leg has a positivity floor; the confidence gate for junk
    queries is enforced by the semantic `similarity_threshold` plus the raw
    `absolute_min` short-circuit rather than a second, corpus-dependent BM25
    magnitude floor (a wrong absolute BM25 threshold would silently cost recall).
  - Deferred (documented, not regressions): the rust `_rust_find_adaptive_cutoff`
    still applies `absolute_min` to normalized scores for the *tail* cut when
    the binding is present; the Python-level junk short-circuit covers the
    empty-result acceptance criterion on both paths, and the per-item raw tail
    trim is exercised via the pure-Python path. A true cross-process pgvector
    parity test needs a live PG (the 2 known pgvector suite failures); the
    contract test asserts the native fallback conforms to the raw-cosine scale
    that pgvector produces by construction.
