# PCF-009 - Later-release Soul cleanup and legacy retirement

- Status: done
- Priority: P0
- Scope: SQLCipher schema purity and irreversible legacy retirement
- Parent: `PCF-000`
- Depends on: `PCF-008`; approved greenfield-release amendment and explicit legacy-retirement approval; verified backups before any real-data destructive maintenance
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-10-09-corefs-retirement-maintenance.md#task-1-retire-legacy-account-credentials-and-soul-schema-pcf-009`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-10-09 12:04 MYT
- Started: 2026-10-09 00:14 MYT
- Completed: 2026-10-09 12:04 MYT

## Goal

In a separate later release, remove converted app data and obsolete ORM mappings from SQLCipher while preserving every internal Soul record and validated provenance.

## Deliverables

- Gated cleanup Alembic target.
- `SoulOwner` with preserved numeric compatibility ID plus opaque Core owner UUID.
- Enumerated retained FK rebuilds and Core-URI provenance conversion.
- App table/model removal, SQLCipher vacuum, and legacy recovery retirement.
- Removal of `memory_vectors`, experience centroid state, and every Soul-side embedding/cache column with unlock-scoped in-memory rebuilds.

## Acceptance

- Pre-apply authorization and post-apply retirement gates pass independently.
- Untouched table hashes match; transformed table hashes match deterministic expected conversions.
- `PRAGMA foreign_key_check` is clean and no `User`/`UserKey` consumers remain.
- SQLCipher schema allowlists contain no search index, embedding, chunk, queue, or access-log state.
- Passphrase/recovery unlock, Runtime rebuild, and clean transfer pass after cleanup.

## Activity Log

- 2026-10-09 12:04 MYT - Completed the explicitly approved final legacy crypto cleanup: removed unused numeric-owner wrappers, old SQLCipher HKDF/Vault parameters, and discarded registration wrapping; retained current native UUID/domain AAD, password/recovery publication, and encryption primitives. Fresh complete backend verification passed 3529 cases across 224 modules (shards [1097, 828, 758, 845]; one Windows POSIX-permissions skip and 11 configured exclusions), active-app build, source/test Ruff, and organization check pass. Independent exact-delta review found no actionable defect. All changes are integrated with hash guards, unrelated edits and real Core data preserved; no external publication. PCF-009 and the initiative are complete locally. The long retirement fixture hit a legitimate idle-thread closure after five minutes; controlled native sweep proved HEAD/catalog/object changes and the correct pre-retirement409 refusal. Closing the fixture thread before capturing its baseline makes maintenance quiescent; the corrected real encrypted single case passes without changing or weakening production checks. The 3529 total combines3528 full-run passes and that corrected single case.

- 2026-10-09 11:12 MYT - The user explicitly instructed 'finish it' in direct response to the exact remaining crypto-helper removal and its approval blocker. Recorded clearance for that proposal and resumed PCF-009 and the parent without changing ownership or Started. Scope is the reviewed three-file removal of obsolete numeric-owner wrapping/SQLCipher derivation helpers and the discarded wrapping step, retaining current native AAD-bound authentication/keyslots. No external publication or actual Core maintenance authorized.

- 2026-10-09 05:13 MYT - Final local integration verified: active-app build, required source/test Ruff, repository organization, 402 desktop/client tests, 3527 distinct configured backend cases, 293 CoreFS tests (one existing helper ignored), and 295 anima-core/PyO3 tests serially pass. The integrated final memory domain passes 190; the extended real encrypted rotation/prune/Runtime-rebuild/key-retirement sequence passes in 258.41 seconds. Independently reviewed final fixes and backed-up hash-guarded integration preserve unrelated edits and actual user data. PCF-010 is complete locally; PCF-009 is blocked only on the specific unapplied obsolete key-wrapping proposal rejected by automatic approval review, requiring explicit user approval. No publication or real-Core maintenance performed.

- 2026-10-09 03:52 MYT - Refreshed child validation with encrypted/current-runtime/schema/agent/native/desktop evidence. Broader backend sweeps continue through current document, memory, and health fixtures; supported archive checkpoint fix and real old-FRK retirement remain under final verification. Unapplied crypto-helper proposal awaits explicit approval; no completed state or real-data maintenance claimed.

- 2026-10-09 03:36 MYT - Native CoreFS 293 and PyO3 lifecycle 2 pass with purge cold-start pointer recovery; v3 binding isolated. Desktop/client 380, active desktop build, required source/test Ruff, maintenance policy/API 26, current schema inventory 7, and focused transfer/activation/migration checks pass. Supported encrypted Full/Soul cleanup and VACUUM interruption/retry preserve independently verified originals; current Runtime privacy 39 passes. Broader backend and agent review regressions continue. Real key-retirement sequence exposed missing checkpoint-catalog inventory references and remains fail-closed under diagnosis. Narrow crypto-helper removal remains unapplied after automatic approval-review rejection, awaiting explicit user approval. Original work is backed up for conflict-free three-way integration; no real Core maintenance or publication performed.

- 2026-10-09 02:16 MYT - Current-only audit retired physical asset/transcript readers and Runtime content routes. Active-app build and Animus check pass; native event/thread tests (13) verify visible metadata through Runtime loss; owner maintenance/thread checks (28), typed desktop confirmation tests (3), and additional source/index fixtures (39) pass. Independent review found and is addressing full Soul credential reopen, current account birthday/discovery, concurrent registration admission, and SQLCipher transactional/vacuum safeguards. Full backend regression and final native bindings/integration remain underway. No real Core changed.

- 2026-10-09 00:14 MYT - User explicitly approved removing all legacy support and continuing remaining CoreFS work. Codex claimed PCF-009 in managed worktree `C:/Users/leoca/.codex/worktrees/corefs-legacy-retirement/animaOS`, from `685e5a13`. Apply the already-approved 2026-08-16 greenfield amendment rather than obsolete pre-release conversion/observation gates. Current-format data preservation and backup/retirement gates remain. Detailed execution: `docs/superpowers/plans/2026-10-09-corefs-retirement-maintenance.md`. Parent ownership preserved; no external publication or real-Core deletion authorized.

- 2026-07-12 06:07 MYT - Ticket created as an explicitly deferred later-release cleanup.
- 2026-08-23 06:40 MYT - Recorded three greenfield-unreachable legacy code
  paths found during PCF-008 closeout review as retirement candidates for this
  ticket: (1) `apps/server/src/anima_server/services/vault.py` retains the
  full V1 vault/capsule import-export machinery (about 2.9k lines) although
  only `export_database_snapshot`/`restore_database_snapshot` are still
  imported (by `db/user_store.py`); (2)
  `db/user_store.py:_migrate_legacy_shared_database_locked` still recognizes a
  legacy shared `anima.db` at the configured `database_url` and migrates it
  into the canonical Soul path at startup, before any release-field authority
  check; (3) `services/agent/inner_monologue.py:run_quick_reflection` falls
  back to the shared `SessionLocal` when no `db_factory` is passed (all
  production call sites pass one). None is reachable on a greenfield install;
  retire them here under this ticket's gates.

- 2026-08-23 18:30 MYT - Post-merge independent review of PR #148 recorded
  eight non-blocking quality findings against the authority boundary, deferred
  here rather than patched the same day the first release merged: (1) three
  copies of the manifest parse/latch/regression sequence in
  `corefs/authority.py` (`read_authority_record`, `core_authority_state_or_none`,
  `latch_manifest_authority`) that must stay in sync; (2) two parallel latch
  sets where one dict would make the subset invariant unbreakable; (3) the
  conversation-cutover guard inlined verbatim at five sites
  (`eager_consolidation`, `persistence`, `thread_manager` x2,
  `transcript_archive`); (4) six near-identical `*_corefs_authority_active`
  wrappers plus nine redundant function-level imports; (5) `_account_marker_active`
  and `_preference_marker_active` validating markers more weakly than the
  shared `_marker_allows`; (6) an unreachable `require_legacy_asset_mutation_allowed`
  call after `canonical_asset_session_or_legacy` in `documents/store.py` and
  `ingestion/sources.py`; (7) `core_authority_state_or_none` re-reading and
  re-parsing the manifest on every content-route call despite the in-process
  latch, several times per agent turn pre-activation; (8) chat history decoding
  every archived thread to serve only the active one. Items 1-6 are
  consolidation work; 7 needs a cache with explicit invalidation because naive
  short-circuiting would remove the damage detection PR #148 rounds 2-3 added.

## Validation

- Final approved cleanup verification (2026-10-09 12:04 MYT): fresh complete backend 3529 passed; one platform skip; 11 configured exclusions; current keyslot/crypto coverage replaces old numeric-wrapper tests. Active-app build, required source/test Ruff, organization check, and independent exact-delta review pass. Changed final paths: `services/crypto.py`, `services/auth.py`, and `tests/test_crypto.py` under `apps/server`, plus linked plans/ticket closeout. Current native password/recovery/SQLCipher publication is unchanged; no actual Core maintenance.
- Integrated original current-keyslot crypto tests:8 passed in1.47seconds. The long retirement fixture hit a legitimate idle-thread closure after five minutes; controlled native sweep proved HEAD/catalog/object changes and the correct pre-retirement409 refusal. Closing the fixture thread before capturing its baseline makes maintenance quiescent; the corrected real encrypted single case passes without changing or weakening production checks. The 3529 total combines3528 full-run passes and that corrected single case.

- Required broader checks: active-app `bun run build`, source/test Ruff, repository organization, and 402 desktop/client tests pass in the integrated original checkout.
- Complete configured backend coverage: 3527 distinct passing cases across all 224 default test modules, with one POSIX-permissions case skipped on Windows and 11 intentionally excluded retrieval/golden cases. The failed distillation caller was fixed; its module and every remaining module were rerun. Final changed memory-domain cases pass 190/190 in the integrated checkout (69.98 seconds).
- Native verification: CoreFS 293 passed, one existing helper ignored; anima-core/PyO3 295 passed serially. Installed v4 matches the verified wheel; previous native package retained externally.
- Independent final requirements/correctness reviews found no remaining actionable defects in implemented scope. Four current documentation consistency checks and diff hygiene pass.

- Focused encrypted schema/auth evidence: 44 retirement/auth tests; seven actual encrypted reopen/snapshot/portability checks with zero skips; supported Full/Soul prior-head preservation, FK/trigger rollback, and VACUUM interruption/retry pass. Retained 28 Soul tables and 50 FK relationships preserve verified counts/hashes and explicit unavailable provenance.
- Current Runtime privacy 39; Runtime format/rejection/indexer/session publication 31; agent service 47; compaction/approval/retry 55; current ingestion/capture/source/LLM-wiki 82 pass.
- Changed paths: every changed path is recorded in the [complete continuation manifest](../../docs/superpowers/plans/2026-10-09-corefs-retirement-maintenance.md#complete-changed-path-manifest), covering schema/models/auth/recovery, source authority, derived state, native conversation/ingestion, active UI/contracts, tests, and documentation.
- Residual implementation blockers: none. Explicit approval for the final helper removal was recorded and the exact patch applied. Current native authentication/keyslots remain intact.
- No actual user Core, schema, Runtime, or recovery original was maintained or deleted. Real provider/hosted CI/signed cross-platform release readiness is not established by these local checks.
