# PCF-001 - Filesystem key hierarchy and credential generations

- Status: in_progress
- Priority: P0
- Scope: `packages/anima-corefs`, `packages/anima-core`, `apps/server`, `apps/desktop`, and `packages/api-client` crypto, manifest, Soul keyslots, credential UI/API
- Parent: `PCF-000`
- Depends on: none
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-1-filesystem-key-hierarchy-and-credential-generations`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-14 04:05 MYT
- Started: 2026-07-13 21:27 MYT
- Completed:

## Goal

Add password/recovery keyslots, Filesystem Root Key subkeys, per-object DEKs, and crash-safe credential generations without changing content authority yet.

## Deliverables

- Versioned manifest and `soul_keyslots` records.
- Stable opaque owner UUID provisioning and complete password/recovery `user_keys` domain backfill before AAD-bound slots activate.
- Password and recovery credential-generation state machines.
- Explicit `full`, `soul`, and `fs` key-completeness scopes; scoped credential replacement preserves degraded/recovery-only state and cannot satisfy full unlock.
- Coordinated change-password and recovery-credential replacement API/Security UI flows.
- FRK v1 provisioning and per-object crypto helpers.
- Canonical native crypto/key helpers in `anima-corefs`, exposed through the existing `anima-core` PyO3 extension with Rust/Python vector parity and no duplicate Python implementation.
- Focused crypto/recovery regression tests.

## Acceptance

- Password and recovery paths unlock every required root and Soul-domain key.
- No raw key or private profile field is added to the new versioned owner/keyslot structures; legacy manifest compatibility fields, including `user_index`, remain until PCF-007.
- Cross-store interruption tests pass at every durable boundary.
- No live password/recovery endpoint can bypass the active manifest/Soul/FRK credential generation.
- Soul-only completeness requires every Soul root/domain key but forbids FRK slots; CoreFS-only completeness requires every retained FRK but forbids SQLCipher/Soul-domain slots.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 15:45 MYT - Added scoped recovery/keyslot completeness and credential-generation requirements for independently recoverable Soul and CoreFS artifacts.
- 2026-07-12 16:01 MYT - Closed review conflict between full-Core recovery and intentional Soul-only/CoreFS-only credential scopes.
- 2026-07-12 17:34 MYT - Assigned CoreFS crypto ownership to Rust and the existing `anima-core` Python extension boundary.
- 2026-07-13 21:27 MYT - Implementation started on `codex/pcf-001-key-hierarchy`; isolated worktree dependencies installed and baseline Rust/crypto/recovery tests verified.
- 2026-07-13 23:13 MYT - Implemented the native opaque FRK/Object-DEK boundary, owner-bound password/recovery generations, typed CoreFS AAD, two-phase recovery confirmation, scoped credential rotation, strict legacy backfill, migration compatibility coverage, and Security UI/API flow; validation is green and the ticket remains `in_progress` pending supervising-agent review.
- 2026-07-14 01:09 MYT - Addressed follow-up security review findings: active versioned roots now gate login/recovery before SQLCipher, Soul crash finalization derives the active scope, genuine CoreFS-only credential replacement is manifest-only, persisted unwrap profiles are bounded, manifest publication is native and durable, and the closed object/FRK/password contracts are enforced. Ticket remains `in_progress` for re-review.
- 2026-07-14 02:11 MYT - Addressed retained-FRK review findings: pending password and recovery generations now verify against the authoritative pre-activation FRK catalog, legacy upgrade uses its protocol-defined v1 catalog without publishing active markers early, and full/FS tamper tests prove failure before activation while the old credential generation remains usable. Ticket remains `in_progress` for re-review.
- 2026-07-14 02:49 MYT - Addressed the fourth follow-up: activated keyslot evidence now prevents legacy fallback when generation markers are removed while PENDING-only legacy-upgrade slots remain non-authoritative; legacy confirmation revalidates both password and recovery generations immediately before activation and reopens both afterward; the desktop supplies the current password only from ephemeral review state. Clarified that PCF-001 preserves legacy manifest compatibility fields for PCF-007. Ticket remains `in_progress` for re-review.
- 2026-07-14 04:05 MYT - Addressed the fifth quality review: registration now publishes legacy login/recovery locators before activating versioned authority; native AAD binds immutable generation/scope/FRK/object-epoch metadata while status transitions in place; unauthenticated CoreFS credential routes share pre-KDF rate/concurrency admission; credential coordinators serialize complete transactions with active-generation CAS; and a live prepared recovery phrase cannot be replaced by a second prepare. Ticket remains `in_progress` for supervising-agent re-review.

## Validation

- Commands:
  - Fifth follow-up TDD: the combined review regression command passed 15 tests after implementation; focused red runs previously failed 9 AAD/schema/registration tests and 6 admission/concurrency tests before implementation.
  - Fifth follow-up full suites: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; .venv\Scripts\python.exe -m pytest -q apps/server/tests/test_corefs_keyslots.py` - 42 passed; the equivalent `test_recovery.py` run - 24 passed.
  - Fifth follow-up crypto/migration bundle: `test_corefs_crypto.py test_corefs_migration.py test_crypto.py test_data_crypto.py` - 26 passed.
  - Fifth follow-up native verification: `cargo test -p anima-corefs` - 10 passed; `cargo test -p anima-core --features python` - 274 passed after adding the uv Python DLL directory to `PATH`; `cargo build -p anima-core --features python` passed. `rg is_none_or packages apps/server/src apps/server/tests` returned no matches for Rust 1.75 source compatibility.
  - Fifth follow-up application verification: `bun run lint:desktop`, `bun run build:desktop`, `bun run lint:server`, and `bun run build:server` passed; `bun run db:server:heads` reported exactly `20260712_0001 (head)`.
  - Fifth follow-up full desktop tests: 55 passed, with 2 unrelated existing navigation failures plus one missing `LayoutTopNav` module-load error; the three recovery-credential replacement tests passed. Whole-workspace `cargo fmt --check` also remains baseline-dirty in unrelated Rust files, which were not modified.
  - Fourth follow-up TDD: the focused backend regression set failed 5 tests before implementation and passed 5 tests afterward; the desktop recovery test failed 2 of 3 tests before implementation and passed 3 of 3 afterward.
  - Fourth follow-up full suites: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; .venv\Scripts\python.exe -m pytest apps/server/tests/test_corefs_keyslots.py -q` - 33 passed; `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; .venv\Scripts\python.exe -m pytest apps/server/tests/test_recovery.py -q` - 21 passed.
  - Fourth follow-up: `bun test apps/desktop/tests/recovery-credential-replacement.test.ts` - 3 passed; `bun run build:desktop`, `bun run lint:server`, and `bun run build:server` passed.
  - Retained-FRK follow-up: targeted legacy-upgrade plus full/FS password/recovery tamper matrix - 5 passed; full `test_corefs_keyslots.py` - 31 passed; full `test_recovery.py` - 18 passed.
  - Retained-FRK follow-up: scoped Ruff, `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run build:server`, and `git diff --check` passed.
  - Follow-up: `.venv\Scripts\maturin.exe develop --manifest-path packages/anima-core/Cargo.toml --features python` rebuilt the editable extension and exported `corefs_atomic_publish`.
  - Follow-up: `cargo test -p anima-corefs` - 9 passed; `cargo test -p anima-core` - 218 passed; total Rust coverage 227 passed.
  - Follow-up: `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; .venv\Scripts\python.exe -m pytest apps/server/tests/test_crypto.py apps/server/tests/test_corefs_crypto.py apps/server/tests/test_corefs_keyslots.py apps/server/tests/test_recovery.py apps/server/tests/test_auth.py -q` - 69 passed.
  - Follow-up under a unique `%TEMP%` `ANIMA_TEST_TEMP_ROOT`: `test_corefs_migration.py` - 1 passed; `test_encrypted_core_regression.py` - 6 passed; `test_crypto.py` - 6 passed. The precedence pair proves a corrupt legacy SQLCipher wrapper cannot override valid versioned roots, while a corrupt active manifest Soul root fails login.
  - Follow-up: `bun test apps/desktop/tests/recovery-credential-replacement.test.ts` - 3 passed; `bun run build:desktop`, `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run build:server`, scoped Ruff, and `bun run lint:server` passed.
  - Follow-up: `.venv\Scripts\alembic.exe -c apps/server/alembic_core.ini heads` - exactly `20260712_0001 (head)`; `git diff --check` passed.
  - `uvx maturin develop --manifest-path packages/anima-core/Cargo.toml --features python` - built and installed the editable PyO3 extension.
  - `cargo test -p anima-corefs -p anima-core` - 226 Rust tests passed.
  - `cargo check -p anima-core --features python` and `rustfmt --edition 2021 --check packages/anima-corefs/src/crypto.rs` - passed (existing Rust warnings only); a whole-file check of the pre-existing `anima-core/src/ffi.rs` reports formatting outside the three PCF hunks, which was deliberately left untouched.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; .venv\Scripts\python.exe -m pytest apps/server/tests/test_corefs_crypto.py -q` - 7 passed.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; .venv\Scripts\python.exe -m pytest apps/server/tests/test_corefs_keyslots.py -q` - 19 passed.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; .venv\Scripts\python.exe -m pytest apps/server/tests/test_corefs_migration.py -q` - 1 passed; fresh upgrade/downgrade/re-upgrade preserved legacy key rows.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; .venv\Scripts\python.exe -m pytest apps/server/tests/test_recovery.py -q` - 16 passed after removal of the obsolete one-phase helper.
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; $env:ANIMA_TEST_TEMP_ROOT=Join-Path $env:TEMP 'animaos-pcf-tests'; .venv\Scripts\python.exe -m pytest apps/server/tests/test_crypto.py apps/server/tests/test_encrypted_core_regression.py -q` - 11 passed.
  - `bun test apps/desktop/tests/recovery-credential-replacement.test.ts` - 2 passed.
  - `bun run build:desktop` and `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run build:server` - passed.
  - scoped `.venv\Scripts\ruff.exe check ...` - passed.
  - `.venv\Scripts\alembic.exe -c apps/server/alembic_core.ini heads` - exactly `20260712_0001 (head)`.
  - `git diff --check` - passed.
- Changed paths:
  - `packages/anima-corefs/`, `packages/anima-core/`, root Cargo workspace/lock
  - `apps/server/src/anima_server/services/corefs/`, auth routes/contracts/user-store, Core manifest/crypto, Soul keyslot model/migration
  - `apps/server/tests/test_corefs_*.py`, `apps/server/tests/test_recovery.py`
  - `apps/desktop/src/pages/settings/`, `apps/desktop/tests/recovery-credential-replacement.test.ts`
  - `packages/anima-auth-contracts/`, `packages/api-client/`
  - `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`, `tickets/portable-core-filesystem/PCF-001-filesystem-key-hierarchy.md`
- Notes:
  - PCF-001 adds versioned owner/keyslot authority without deleting legacy manifest compatibility fields. `user_index` and the remaining legacy compatibility surface are intentionally retained until the PCF-007 cutover.
  - Portability tests must set `ANIMA_TEST_TEMP_ROOT` outside the OneDrive-synchronized checkout on Windows; the same isolated test failed only when OneDrive held the directory and passed immediately under `%TEMP%`.
  - FRK generation/wrapping/unwrapping and Object-DEK generation/wrapping/unwrapping remain opaque native handles. Python retains only existing Soul/SQLCipher and legacy UserKey byte handling.
