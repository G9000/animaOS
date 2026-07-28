# MIH-003 - Triage the pre-existing test-failure baseline

- Status: in_progress
- Priority: P1
- Scope: `apps/server/tests`, CoreFS/keyslots/recovery/vault domain
- Parent: none
- Depends on: none
- Owner: Claude
- PRD: none
- Plan: none
- Created: 2026-07-19 03:34 MYT
- Updated: 2026-07-19 03:34 MYT
- Started: 2026-07-22 MYT
- Completed:

## Goal

Determine whether the persistent full-suite failures are real regressions or environment artifacts, fix or quarantine them, and restore a green baseline so feature PRs can detect their own regressions.

## Context

The full suite has carried a growing set of failures throughout the Inner Life v1 work: **47 during IL-001/002/004, drifting to 54 during IL-005** as more main PRs merged (CoreFS/keyslots/recovery/vault + `test_dev_session_continuity`). Every Inner Life PR verified "no NEW failures" by byte-comparing against a stashed baseline — which works but is fragile and hides real regressions in that domain. Two of those failing files (`test_vault.py`) are now load-bearing for IL-005's right-to-forget guarantees, so the vault suite specifically must go green. An earlier read-only triage attempt stalled but noted the venv rebuilds sqlcipher3 cleanly, leaving the real-regression-vs-environment question open.

## Deliverables

- Root-cause classification of the ~54 failures: real regression (name the introducing PR) vs environment/local-config (name the fix) vs flaky/order-dependent.
- Fixes for real regressions; quarantine markers (with tracking) for anything deferred.
- Restored green baseline (or a documented, minimal known-failure allowlist enforced in CI rather than by manual stash-comparison).
- **Suite-hygiene item (discovered during IL-003):** some test in the full suite runs a `git clean`/filesystem sweep that DELETES untracked working-tree files mid-run. During IL-003 this silently removed the not-yet-committed new source files (`drives.py`, `initiative.py`, the `.j2` template), producing 5 phantom `getsource`/`FileNotFoundError` failures that vanished the instant the files were `git add`ed. This is a footgun for every feature branch before its first commit and can also mask real breakage. Locate the offending test, make it scope its cleanup (e.g. a temp dir) instead of touching the repo working tree, or guard it so it never removes untracked files outside its own fixture.

## Acceptance

- `bun run test` is green, or fails only on an explicit, CI-enforced allowlist with a tracking issue per entry.
- The `test_vault.py` failures (load-bearing for right-to-forget) are resolved.

## Activity Log

- 2026-07-19 03:34 MYT - Ticket created; baseline drift observed across PRs #98/#104/#108/#112.
- 2026-07-20 MYT - Baseline holds at 54 failed / 2821 passed / 2 skipped through IL-003 (CoreFS/keyslots 29, recovery 18, p5-transcript-archive 3, vault 2, encrypted-core-regression 2). Added the untracked-file-deletion suite-hygiene item after it produced 5 phantom failures on the IL-003 branch pre-commit.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Baseline: 54 failed / ~2610 passed as of IL-005 merge (2026-07-19).
