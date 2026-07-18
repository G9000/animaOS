# IL-005 - Forgetting as distillation (F7 extension)

- Status: done
- Priority: P2
- Scope: `apps/server/src/anima_server/services/agent/forgetting.py`, `apps/server/src/anima_server/services/agent/claims.py`, `apps/server/src/anima_server/models`, `apps/server/src/anima_server/services/agent/distillation.py` (new), `apps/server/src/anima_server/services/agent/sleep_agent.py`, `apps/server/src/anima_server/services/vault.py`
- Parent: `IL-000`
- Depends on: none
- Owner: Claude
- PRD: docs/prds/presence/inner-life-v1.md
- Plan: docs/superpowers/plans/2026-07-15-inner-life-v1.md
- Created: 2026-07-15 16:55 MYT
- Updated: 2026-07-18 16:40 MYT
- Started: 2026-07-18 15:20 MYT
- Completed: 2026-07-18 16:40 MYT

## Goal

Make passive forgetting distill instead of delete: decayed casual/transient/emotional-pattern items dissolve into semantic tendency claims plus a content-free tombstone, while anchored classes and user-initiated deletion keep existing F7 semantics.

## Deliverables

- Distillation step in the F7 decay path: affective/topical signature → `tendency` namespace claim, `origin: distilled`.
- `tendency_contributions` ledger (tombstone_id, tendency_claim_id, contribution_vector — numeric only, no content); tendency values recomputable from surviving ledger rows. Ledger and tombstones are soul-store scoped and included in vault export/import (they cannot be rebuilt after content deletion).
- Tombstone rows retaining only memory class, affect label, and time range; content/embeddings/evidence cryptographically deleted.
- `forget_audit_log` mode `distilled`.
- User-initiated deletion of a distilled item deletes its ledger rows and recomputes affected tendencies (right-to-forget precedence).
- Class exemptions: identity, life_event, relationship never distill.

## Acceptance

- Distilled tendencies retrievable as semantic claims; tombstones and ledger rows verified content-free via export.
- Property test: distill → explicit forget ≡ never distilled (exact tendency recomputation).
- Exempt classes follow unchanged F7 behavior (regression tests pass).

## Activity Log

- 2026-07-15 16:55 MYT - Ticket created.
- 2026-07-15 17:40 MYT - Added tendency_contributions ledger per review: EMA-merged aggregates made explicit forget of already-distilled items impossible; tendencies now recompute from surviving ledger rows.
- 2026-07-18 16:40 MYT - Implemented: `distillation.py` (new module — distill sweep, deterministic tendency phrase/strength, `recompute_tendency_from_ledger` single source of truth), `claims.upsert_tendency_claim` (topic-keyed, not content-keyed, so recomputed strength never supersedes the claim), `TendencyContribution` model + `distilled_at` column + migration `20260718_0001` (chained on IL4's `20260717_0001` head), `_task_heat_decay` wired to distill post-commit with its own per-item commit/rollback loop (mirrors IL4 crystallization), `forget_memory` right-to-forget scrub/recompute hook, retrieval-visibility guards (`get_memory_items`, `get_memory_items_scored`, `load_canonical_memory_retrieval_documents`, memory-overview counts route), and vault export/import for `memoryClaims` + `tendencyContributions` (MemoryClaim was not previously vault-scoped at all — extended, since PRD §5 requires tendency claims to survive export/import). 17 tests in `test_inner_life_distillation.py` (RED confirmed via `git stash` before GREEN). Found and fixed a real regression during full-suite validation: the naive migration broke `test_stamped_soul_database_migration_repairs_missing_new_tables` (ALTER on a not-yet-created `memory_items` in a legacy-repair scenario) — fixed with the same `_has_table` guard pattern `20260701_0003_add_memory_salience.py` already uses.
- 2026-07-18 19:29 MYT - Task review Approved (zero Critical/Important; two minors). Final whole-branch review "With fixes": structural focus-query guards (2d77abe), claim-evidence delete before claims on vault import + scored-only sweep predicate (1140869) — applied. Full suite 54 failed / 2591 passed = drifted pre-existing baseline (was 47; drift from recent main merges, verified not this branch).

## Validation

- Commands:
  - `uv run --project apps/server pytest apps/server/tests/test_inner_life_distillation.py` — 17 passed
  - `uv run --project apps/server pytest apps/server/tests/test_forgetting.py apps/server/tests/test_inner_life_latent.py apps/server/tests/test_memory_api.py apps/server/tests/test_vault.py apps/server/tests/test_sleep_agent.py apps/server/tests/test_runtime_db.py` — 211 passed, 2 failed (pre-existing vault/keyslot baseline failures, confirmed identical with changes stashed out)
  - `bun run test` — 54 failed / 2591 passed; failing set verified byte-identical to a stashed-out baseline run (CoreFS/keyslots/recovery/vault only — `test_dev_session_continuity` was not failing in either run, so the actual current baseline is 54, not the 47 noted in the brief; this is pre-existing drift, not something IL-005 introduced)
- Changed paths:
  - see commit `IL-005: add forgetting-as-distillation` on `feature/il-005-distillation`
- Notes:
  - MemoryClaim/MemoryClaimEvidence were not vault-scoped before this ticket (`_MEMORY_TABLES` etc. never listed them) despite the PRD binding tendency claims to vault export/import — extended vault.py to include MemoryClaim (not MemoryClaimEvidence, which tendency claims never populate; see report for detail).
  - Verified the ONLY other MemoryItem hard-delete path (`redact_derived_references`'s pattern-item cleanup, which bypasses `forget_memory`) cannot reach a distilled item: it matches on content/evidence substrings, and a distilled item's content is emptied and its evidence hard-deleted, so it can never satisfy that path's match condition. No second scrub hook needed.
