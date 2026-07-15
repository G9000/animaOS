# IL-004 - Latent trace buffer and crystallization

- Status: backlog
- Priority: P2
- Scope: `apps/server/src/anima_server/services/agent/consolidation.py`, `apps/server/src/anima_server/services/agent/sleep_tasks.py`, `apps/server/src/anima_server/models`
- Parent: `IL-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: none
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-15 16:55 MYT
- Started:
- Completed:

## Goal

Stop silently dropping sub-threshold memory candidates: accumulate them as weighted latent traces per topic and synthesize a fully-provenanced memory when cumulative weight crosses the crystallization threshold.

## Deliverables

- `latent_traces` table (topic_key, kind, weight, evidence_refs, first_seen, last_seen) + migration, soul-store scoped.
- Consolidation hook: candidates in [0.25× threshold, threshold) fold into traces via EMA (0.9/0.1) with evidence refs.
- Sleep-time crystallization task: topic weight ≥ θ_c → synthesize one `origin: crystallized` memory listing all contributing evidence, clear topic.
- Weekly trace decay (×0.98) and table cap.

## Acceptance

- Above-floor candidates are never dropped without a trace write.
- Crystallized memories carry complete evidence provenance.
- Duplicate-topic churn does not double-count (claim-slot dedup reused).
- Trace table bounded under sustained load (test).

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - none
