# IL-004 - Latent trace buffer and crystallization

- Status: backlog
- Priority: P2
- Scope: `apps/server/src/anima_server/services/agent/consolidation.py`, `apps/server/src/anima_server/services/agent/sleep_tasks.py`, `apps/server/src/anima_server/models`
- Parent: `IL-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-15 17:10 MYT
- Started:
- Completed:

## Goal

Stop silently dropping sub-threshold memory candidates: accumulate them as weighted latent traces per topic and synthesize a fully-provenanced memory when cumulative weight crosses the crystallization threshold.

## Deliverables

- `latent_traces` table (topic_key, kind, weight, evidence_refs, first_seen, last_seen) + migration, soul-store scoped.
- New candidate scoring/rejection flow (none exists today — plan_candidate_promotion() never rejects by score): normalized score `s = clamp01(0.6·importance/5 + 0.3·emotional_salience + 0.1·evidence_strength)` with promotion threshold θ_p, calibrated behavior-preserving (importance ≥ 2 promotes as today).
- Extraction prompt update: emit `minor_observation` candidates currently omitted (the actual source of sub-threshold volume).
- Consolidation hook: candidates with s in [0.25·θ_p, θ_p) fold into traces additively (`weight ← min(1.0, weight + 0.5·s)`, leaky integrator with weekly decay as the leak) with evidence refs.
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

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
