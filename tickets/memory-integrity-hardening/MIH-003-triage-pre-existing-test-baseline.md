# MIH-003 - Triage the pre-existing test-failure baseline

- Status: done
- Priority: P1
- Scope: `apps/server/tests`, CoreFS/keyslots/recovery/vault domain
- Parent: none
- Depends on: none
- Owner: Claude
- PRD: none
- Plan: none
- Created: 2026-07-19 03:34 MYT
- Updated: 2026-07-28 22:31 MYT
- Started: 2026-07-28 13:32 MYT
- Completed: 2026-07-28 22:31 MYT

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
- 2026-07-28 13:32 MYT - Claimed (backlog/unassigned -> in_progress/Claude) and started triage on `feature/mih-003-baseline-triage`.
- 2026-07-28 13:38 MYT - **Triage complete: ZERO real code regressions.** Three stacked causes:
  1. **Environment (`.env.local`)** — `ANIMA_CORE_PASSPHRASE` leaks through pydantic-settings into every pytest process, flipping the server into env-passphrase mode; registration then skips versioned key-hierarchy provisioning (`_maybe_generate_sqlcipher_key` returns None), so ~51 CoreFS/keyslots/recovery/vault/encrypted-core tests failed (0 SoulKeyslots; "credential generation has an ambiguous scope" on login). Fix: conftest forces `ANIMA_CORE_PASSPHRASE=""` before settings import (mirrors the existing embedding-provider hermeticity guard); passphrase-mode tests still opt in via monkeypatch.
  2. **Environment (stale native build)** — the installed `anima_core` wheel predated `CorefsSession` (present in `packages/anima-core/src/ffi.rs`); surfaced as `AttributeError` once cause 1 was fixed. Fix: `uv sync --reinstall-package anima-core`, plus a conftest fail-fast guard that names that exact command instead of emitting dozens of cryptic per-test errors.
  3. **Test drift** — `eager_consolidation` renamed `get_active_dek` → `get_active_dek_async` (module-level import) but `test_p5_transcript_archive` still patched the old sync name (`AttributeError` in `mock.patch`); 5 tests (3 visible in the old baseline + 2 masked by cause 1). Fix: patch sites updated to the async seam with `AsyncMock`.
  - The failure counts were mode-dependent, which is why the "baseline" drifted (47→54) as unrelated PRs merged.

- 2026-07-28 21:40 MYT - Second stratum fixed (unmasked by the green baseline; all reproduce on unmodified main): async unlock-deps migration drift across chat (3), capabilities (2), health_api (5), user_profile (2), dashboard config-route (3) — stale patch targets updated to the *_async seams; embedding-hermeticity interaction in test_http_backend_status (explicit provider now set per the conftest guard's contract); platform-sensitive Windows-path assertion in the catalog-benchmark CLI-contract test.
- 2026-07-28 22:15 MYT - Order-dependence poisoner found by bisection (150 files -> 1): test_corefs_package popped corefs/corefs.logical/corefs.types from sys.modules without restoring, minting duplicate PayloadScope/KeyslotStatus enum classes — identity comparisons in keyslots then matched nothing, 401-failing 21 recovery/vault/encrypted-core tests at full-suite scale ("valid phrase -> ambiguous scope"). Fixed with snapshot/restore of the popped modules plus re-binding the original corefs attribute on the parent services package (attribute-walking resolvers bypass sys.modules).
- 2026-07-28 22:31 MYT - **Acceptance met: `bun run test` -> 3124 passed, 0 failed, 10 skipped** — first fully green suite; no allowlist needed. Sentinel untracked files survived two complete suite runs: no test deletes untracked working-tree files; the IL-003 "phantom deletion" is attributed to concurrent agent git operations. Done pending PR #121 merge (user).

## Validation

- Commands:
  - `uv run pytest tests/test_corefs_keyslots.py -q` -> 51 passed (was 29 failed)
  - `uv run pytest tests/test_recovery.py tests/test_vault.py tests/test_encrypted_core_regression.py tests/test_p5_transcript_archive.py -q` -> all passed (was 25 failed)
  - `bun run test` -> **3124 passed, 0 failed, 10 skipped** (was 54 failed)
- Changed paths:
  - `apps/server/tests/conftest.py` (hermeticity: force unified mode; fail-fast on stale anima_core)
  - `apps/server/tests/test_p5_transcript_archive.py` (patch the renamed async DEK seam)
  - `apps/server/tests/{test_chat,test_capabilities_api,test_health_api,test_user_profile,test_dashboard_api}.py` (async unlock-deps patch targets)
  - `apps/server/tests/test_corefs_catalog_benchmark.py` (platform-agnostic path comparison)
  - `apps/server/tests/test_corefs_package.py` (sys.modules snapshot/restore — the 21-test poisoner)
- Notes:
  - No production code changed — the entire baseline was environment + test drift.
  - The suite-hygiene "untracked file deletion" item could not be attributed to any test (no `git clean`/repo-tree `rmtree` exists in the suite); the leading explanation is concurrent git checkpointing by a background agent during IL-003. Sentinel untracked files planted through a full-suite run to confirm.
