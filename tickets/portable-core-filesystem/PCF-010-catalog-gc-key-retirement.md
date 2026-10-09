# PCF-010 - Retained-catalog garbage collection and key retirement

- Status: done
- Priority: P0
- Scope: Core catalog/object maintenance, cryptographic deletion, key retirement, Security UI
- Parent: `PCF-000`
- Depends on: `PCF-008`, accepted retention policy, verified current backup, no active transfer/rotation/migration
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-10-09-corefs-retirement-maintenance.md#task-2-authenticated-retention-and-garbage-collection-pcf-010`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-10-09 05:13 MYT
- Started: 2026-10-09 00:20 MYT
- Completed: 2026-10-09 05:13 MYT

## Goal

Safely reclaim unreachable catalog/object revisions and retire decrypt-only key material through an authenticated, resumable, backup-aware maintenance flow.

## Deliverables

- Explicit restore-window and pin policy with dry-run/doctor inventory.
- Authenticated mark/quarantine/sweep with Core-wide maintenance locking and crash-safe resume.
- Honest cryptographic-deletion reporting across local history and known backups.
- User-only `purge(trash_id, expected_trash_revision, confirmation)` for already-trashed content with recent reauthentication and one-use bound confirmation.
- Gated FRK/Object-DEK retirement API, Security UI, and reopen/restore verification.

## Acceptance

- No reachable or pinned catalog/object/key is removed under concurrency, corruption, crash, transfer, rotation, or migration tests.
- Pruning is idempotent and a changed `fs/HEAD` aborts/recomputes before deletion.
- Key retirement requires zero retained references, verified active-generation backup, and successful passphrase/recovery reopen before and after slot removal.
- UI clearly distinguishes local pruning from SSD/backup erasure and requires explicit irreversible confirmation.
- Live objects, stale trash revisions, unconfirmed recursive inventories, ANIMA/client callers, active-operation pins, and replayed confirmations cannot purge; wrapped Object DEKs retire only after zero local retained references are proven.

## Activity Log

- 2026-10-09 05:13 MYT - Final local integration verified: active-app build, required source/test Ruff, repository organization, 402 desktop/client tests, 3527 distinct configured backend cases, 293 CoreFS tests (one existing helper ignored), and 295 anima-core/PyO3 tests serially pass. The integrated final memory domain passes 190; the extended real encrypted rotation/prune/Runtime-rebuild/key-retirement sequence passes in 258.41 seconds. Independently reviewed final fixes and backed-up hash-guarded integration preserve unrelated edits and actual user data. PCF-010 is complete locally; PCF-009 is blocked only on the specific unapplied obsolete key-wrapping proposal rejected by automatic approval review, requiring explicit user approval. No publication or real-Core maintenance performed.

- 2026-10-09 03:52 MYT - Refreshed child validation with encrypted/current-runtime/schema/agent/native/desktop evidence. Broader backend sweeps continue through current document, memory, and health fixtures; supported archive checkpoint fix and real old-FRK retirement remain under final verification. Unapplied crypto-helper proposal awaits explicit approval; no completed state or real-data maintenance claimed.

- 2026-10-09 03:36 MYT - Native CoreFS 293 and PyO3 lifecycle 2 pass with purge cold-start pointer recovery; v3 binding isolated. Desktop/client 380, active desktop build, required source/test Ruff, maintenance policy/API 26, current schema inventory 7, and focused transfer/activation/migration checks pass. Supported encrypted Full/Soul cleanup and VACUUM interruption/retry preserve independently verified originals; current Runtime privacy 39 passes. Broader backend and agent review regressions continue. Real key-retirement sequence exposed missing checkpoint-catalog inventory references and remains fail-closed under diagnosis. Narrow crypto-helper removal remains unapplied after automatic approval-review rejection, awaiting explicit user approval. Original work is backed up for conflict-free three-way integration; no real Core maintenance or publication performed.

- 2026-10-09 02:16 MYT - Current-only audit retired physical asset/transcript readers and Runtime content routes. Active-app build and Animus check pass; native event/thread tests (13) verify visible metadata through Runtime loss; owner maintenance/thread checks (28), typed desktop confirmation tests (3), and additional source/index fixtures (39) pass. Independent review found and is addressing full Soul credential reopen, current account birthday/discovery, concurrent registration admission, and SQLCipher transactional/vacuum safeguards. Full backend regression and final native bindings/integration remain underway. No real Core changed.

- 2026-10-09 00:58 MYT - Native retention/prune/purge/reanchor/held-lock bindings are implemented in isolation; 286 native unit tests pass with one existing helper ignored. Python owner-only maintenance, both-credential reauthentication, current full-backup verification, one-use confirmation, admission exclusion, and Security UI are implemented; 25 API/safety tests, 2 desktop confirmation tests, 30 API-client tests, and desktop production build pass. Integrated current-Core backup/prune/reopen, failure seams, independent code review, and full release checks remain underway. No real Core maintenance performed.

- 2026-10-09 00:20 MYT - User approved continuation of remaining CoreFS work and selected 30 days restore history. Codex claimed PCF-010 on `g9000/corefs-retirement-maintenance` in managed `corefs-legacy-retirement/animaOS` worktree after independent plan review passed. PCF-008 is done. Backup, no-active-operation, complete authenticated reachability, bound confirmation, and reopen checks are runtime gates for destructive actions, not waived by this implementation claim. Current trash never expires merely because history ages. No actual user Core maintenance or external publication performed.

- 2026-07-12 06:07 MYT - Ticket created as separately gated post-cutover maintenance.
- 2026-07-12 17:34 MYT - Added the concrete user-reauthenticated trash purge boundary and adversarial authorization/precondition tests.

## Validation

- Required broader checks: active-app `bun run build`, source/test Ruff, repository organization, and 402 desktop/client tests pass in the integrated original checkout.
- Complete configured backend coverage: 3527 distinct passing cases across all 224 default test modules, with one POSIX-permissions case skipped on Windows and 11 intentionally excluded retrieval/golden cases. The failed distillation caller was fixed; its module and every remaining module were rerun. Final changed memory-domain cases pass 190/190 in the integrated checkout (69.98 seconds).
- Native verification: CoreFS 293 passed, one existing helper ignored; anima-core/PyO3 295 passed serially. Installed v4 matches the verified wheel; previous native package retained externally.
- Independent final requirements/correctness reviews found no remaining actionable defects in implemented scope. Four current documentation consistency checks and diff hygiene pass.

- Owner-only policy/API safety 26 pass; adjacent archive/activation/transfer/GC checks 88 pass. Real current-Core rotation/checkpoint/prune/replay/resume/Runtime-loss rebuild/FRK retirement passes in 258.41 seconds in the original checkout. Independent full archive restore and inventory, zero old-key references, both full credential reopen paths before/after removal, token revocation, unchanged authored bytes/history/metadata, plaintext-free rebuilt Runtime projections, and `/health` 200 are verified.
- Native adversarial inventory/prune/purge/preparation/concurrency/corruption/pointer-recovery checks pass. UI confirmation tests and the broader desktop/client suite verify current review semantics.
- Changed paths: every changed path is recorded in the [complete continuation manifest](../../docs/superpowers/plans/2026-10-09-corefs-retirement-maintenance.md#complete-changed-path-manifest), including native transaction/maintenance/archive/PyO3, `corefs/gc.py`, admission/credentials/transfer, API/schema/contracts, Security UI, and regressions.
- Residual limitations: local verification only; SSD physical remnants and exported backups are not erased by local purge. The user-selected restore window is 30 days; live content, current trash, unknown age, and authenticated pins remain protected. No actual user Core maintenance or external publication performed. PCF-009's specific crypto-helper approval remains separate.
