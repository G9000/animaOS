# IL-004 - Latent trace buffer and crystallization

- Status: done
- Priority: P2
- Scope: `apps/server/src/anima_server/services/agent/consolidation.py`, `apps/server/src/anima_server/services/agent/soul_writer.py`, `apps/server/src/anima_server/services/agent/sleep_tasks.py`, `apps/server/src/anima_server/models`
- Parent: `IL-000`
- Depends on: none
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-17 18:00 MYT
- Started: 2026-07-17 16:00 MYT
- Completed: 2026-07-17 18:00 MYT

## Goal

Stop silently dropping sub-threshold memory candidates: accumulate them as weighted latent traces per topic and synthesize a fully-provenanced memory when cumulative weight crosses the crystallization threshold.

## Deliverables

- `latent_traces` table (topic_key, kind, weight, evidence_refs, first_seen, last_seen) + migration, soul-store scoped.
- New candidate scoring/rejection flow (none exists today — plan_candidate_promotion() never rejects by score): normalized score `s = clamp01(0.6·importance/5 + 0.3·emotional_salience + 0.1·evidence_strength)` with promotion threshold θ_p, calibrated behavior-preserving (importance ≥ 2 promotes as today).
- Extraction prompt update: emit `minor_observation` candidates currently omitted (the actual source of sub-threshold volume).
- Threshold hook in the live promotion path — `plan_candidate_promotion()` in `soul_writer.py` (not only around extraction/consolidation): candidates with s in [0.25·θ_p, θ_p) return a new `fold_to_trace` decision instead of promote, folding into traces additively (`weight ← min(1.0, weight + 0.5·s)`, leaky integrator with weekly decay as the leak) with evidence refs.
- Sleep-time crystallization task: topic weight ≥ θ_c → synthesize one `origin: crystallized` memory listing all contributing evidence, clear topic.
- Weekly trace decay (×0.98) and table cap.
- F7 integration: explicit forget (single-item and topic-scoped) deletes matching traces and scrubs evidence_refs to forgotten sources; crystallization re-validates refs at synthesis time.

## Acceptance

- Above-floor candidates are never dropped without a trace write.
- Crystallized memories carry complete evidence provenance.
- Duplicate-topic churn does not double-count (claim-slot dedup reused).
- Trace table bounded under sustained load (test).
- Forgotten evidence can never crystallize: forget-then-sleep test proves no memory synthesizes from removed refs.

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.
- 2026-07-15 17:25 MYT - Switched trace update from EMA to additive leaky integrator per review (EMA converges below threshold and never crystallizes).
- 2026-07-15 17:40 MYT - Brought latent traces inside the F7 deletion boundary per review (forget scrubs traces/refs; crystallization re-validates refs).
- 2026-07-15 18:15 MYT - Specified the candidate scoring/rejection flow explicitly per review: no promotion threshold exists today, so IL4 adds one (behavior-preserving default) plus minor_observation extraction.
- 2026-07-15 18:45 MYT - Added soul_writer.py to scope per review: the threshold hook must live in plan_candidate_promotion(), the live promotion decision path.
- 2026-07-17 18:00 MYT - Implemented: pure scorer/fold/decay (`inner_life/latent.py`), soul-store edges (`latent_traces.py`), `LatentTrace` model + migration, `plan_candidate_promotion()` scoring gate (dedup wins over folding), `minor_observation` extraction category, weekly decay/cap + crystallization sleep tasks, F7 forget scrub (source-based + topic-scoped), vault export/import. 32 new tests in `test_inner_life_latent.py`; full suite 2470 passed / 47 pre-existing failures (unchanged baseline).

## Validation

- Commands:
  - `uv run --project apps/server pytest apps/server/tests/test_inner_life_latent.py` — 32 passed
  - `bun run test` — 2470 passed, 47 failed (pre-existing CoreFS/keyslots/recovery/vault + test_dev_session_continuity baseline, unchanged)
- Changed paths:
  - see commit `IL-004: add latent trace buffer and crystallization` on `feature/il-004-latent-traces`
- Notes:
  - `delta_extraction.md.j2` intentionally NOT updated with `minor_observation` — that prompt targets surprising/contradictory/corrective signals, the semantic opposite of a passing minor observation.
