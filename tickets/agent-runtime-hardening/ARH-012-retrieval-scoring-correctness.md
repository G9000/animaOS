# ARH-012 - Retrieval scoring correctness

- Status: backlog
- Priority: P2
- Scope: `apps/server`
- Parent: `ARH-000`
- Depends on: none
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-07-07-agent-runtime-hardening.md
- Created: 2026-07-07 00:28 MYT
- Updated: 2026-07-07 00:28 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
