# PCF-003 - Machine-local Runtime and progressive indexing

- Status: in_progress
- Priority: P0
- Scope: `apps/server` runtime/indexing, desktop readiness/security UI
- Parent: `PCF-000`
- Depends on: `PCF-002`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-3-machine-local-runtime-and-progressive-indexing`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-30 11:44 MYT
- Started: 2026-07-29 01:57 MYT
- Completed:

## Goal

Move Runtime outside `.anima/`, add resumable migration/index state, progressively rebuild search after unlock, and expose safe readiness and FRK rotation operations.

## Deliverables

- Instance-scoped relocation for PostgreSQL, `.anima/indices`, health logs, blind tokens, checkpoints, and caches; machine-wide Tauri daemon path; durable migration journal.
- Machine-local Core-path/filesystem-identity registry and lease preventing divergent same-`core_id` copies from sharing mutable state, including explicit runtime-URL collision checks.
- Safe catalog/blind-token persistence with memory-only plaintext search and embeddings.
- Unlock-derived runtime sealing for crash-durable sensitive operational payloads; rebuildable chunks/OCR/source spans remain memory-only.
- Lock/logout/shutdown teardown for all decrypted state.
- Readiness and key-rotation API/desktop surfaces with executed tests.

## Acceptance

- `.anima/` contains no PostgreSQL directory.
- Static path-inventory tests reject Runtime indexes, logs, daemon files, or other unapproved writers under `.anima/`.
- Lock makes decrypted search/read unavailable and clears keys/vectors/query state.
- Deleted Runtime rebuilds without canonical data loss.
- Raw PostgreSQL/runtime-disk scans find none of the seeded message, chunk, OCR, source, candidate, pending-op, preview, or vector plaintext markers.
- Moved Core, same-machine transfer copy, stale lease, simultaneous clone, and runtime-URL collision tests never mix Runtime state.
- Rotation status/progress/recovery gates are operable through Security settings.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-29 01:57 MYT - Claimed PCF-003 from merged `main` (`7a390eb3`) on branch `codex/pcf-003-runtime-indexing` in `.worktrees/pcf-003-runtime-indexing`. The isolated baseline passed `35` focused backend tests, the desktop production build, and `cargo check -p desktop`; implementation will follow the approved Task 3 plan test-first.
- 2026-07-29 13:49 MYT - Completed the machine-local relocation and unlock-scoped indexing foundation. The Runtime now has instance-bound opaque catalog/checkpoint/blind-token/migration/sealed-payload models at migration head `033_corefs_runtime_index`, HKDF-derived sealing and blind-index subkeys, atomic blind-token generations, progressive/degraded readiness state, and one teardown path attached to unlock sessions. Focused relocation/privacy/index/API/session/runtime validation passed `121` tests with `8` environment-dependent embedded-PostgreSQL skips.
- 2026-07-29 14:07 MYT - Added authenticated catalog reconciliation, private-text-free progress/rotation status, resumable two-credential FRK rotation through the native CoreFS catalog transaction, unlock-token replacement after cutover, atomic blind-index generation reset, the desktop readiness provider and Security UI, and the root `test:desktop` command. Server status/index/API reconciliation passed `52` focused tests, the complete existing keyslot band plus new API coverage passed `55`, desktop readiness/rotation passed `3`, the desktop production build passed, and both the Python-enabled `anima-core` binding and Tauri host compile.
- 2026-07-29 14:55 MYT - Completed PCF-003 implementation validation on current `main`. The fresh server integration and credential bands passed `162` tests with `8` environment-dependent embedded-PostgreSQL skips; the complete desktop suite passed `77/77`; the desktop production build, Tauri host, Python-enabled `anima-core`, focused native CoreFS binding tests, scoped Ruff, Rust formatting, and diff hygiene passed. The root desktop runner also exposed and repaired three stale mainline test contracts for the current HUD navigation and recovery replacement payload. The child and parent remain `in_progress` pending a clean current-head review and the required second-phase metadata closeout.
- 2026-07-29 15:37 MYT - Fixed PR #127's failed standalone release-notice gate test-first. Release staging already wrote to `resources/runtime`, but the provenance workflow, notice validator default/resource-map contract, and Tauri resource ignore still named obsolete `resources/.anima` paths. The new static contract failed RED, then the complete `77/77` desktop suite, exact legal-only staging plus notice validation, scoped Ruff, and diff hygiene passed GREEN. PCF-003 and its parent remain `in_progress` pending refreshed CI/current-head review and second-phase closeout.
- 2026-07-29 15:56 MYT - Addressed all four actionable P1 findings from PR #127's review of `89f98dfb` test-first. Catalog refresh now preserves the unlock-scoped local-instance binding; legacy PostgreSQL remains the active source until converter cutover; legacy `runtime-config.json` moves by verified copy/delete into instance config without journal secrets; and explicit Runtime databases atomically claim a database-local singleton before pgvector or Alembic. Five focused regressions failed RED and passed GREEN; the expanded startup/schema/privacy band passed `49` with `8` embedded-PostgreSQL skips, and the full PCF-003 integration band passed `108` with the same `8` skips. Scoped Ruff, repository organization, and diff hygiene pass. The child and parent remain `in_progress` pending refreshed CI/current-head review and second-phase closeout.
- 2026-07-30 11:44 MYT - Addressed PR #127's three new current-head findings test-first. Repeat updates now replace the encrypted candidate payload while leaving mapped Runtime columns as non-plaintext placeholders; security status schedules at most one background rebuild per unlock-scoped index instead of running the native walk inline; and native walk failures publish a counted degraded `unknown` family. Four focused regressions failed RED and passed GREEN, the related privacy/security/migration/index/candidate band passed `43`, and the full PCF-003 server band passed `111` with `8` environment-dependent embedded-PostgreSQL skips. Scoped Ruff and diff hygiene pass. The child and parent remain `in_progress` pending refreshed CI/current-head review and second-phase closeout.

## Validation

- Commands:
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_api.py apps/server/tests/test_corefs_indexer.py apps/server/tests/test_corefs_instance_registry.py apps/server/tests/test_corefs_legacy_runtime.py apps/server/tests/test_corefs_migration.py apps/server/tests/test_corefs_path_inventory.py apps/server/tests/test_corefs_runtime_privacy.py apps/server/tests/test_corefs_security_api.py apps/server/tests/test_health_startup.py apps/server/tests/test_runtime_db.py -q` (`111` passed, `8` skipped)
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_crypto.py apps/server/tests/test_corefs_keyslots.py -q` (`58` passed)
  - `bun run test:desktop` (`77` passed)
  - `bun run --cwd apps/desktop build`
  - `cargo check -p desktop`
  - `$env:PYO3_PYTHON=(Resolve-Path '.venv/Scripts/python.exe').Path; cargo check -p anima-core --features python`
  - `$env:PYO3_PYTHON=(Resolve-Path '.venv/Scripts/python.exe').Path; cargo test -p anima-core --features python corefs_` (`23` passed)
  - `uv run ruff check <changed Python paths>`
  - `cargo fmt --package anima-core -- --check`
  - `git diff --check`
- Changed paths:
  - `apps/server/alembic_runtime/`, `apps/server/src/anima_server/{api,db,models,schemas,services}/`, `apps/server/src/anima_server/{config,main}.py`, and focused server tests
  - `packages/anima-core/`, `packages/api-client/`, and `package.json`
  - `apps/desktop/src/`, `apps/desktop/src-tauri/`, `apps/desktop/tests/`, and `scripts/prepare-desktop-release.ts`
  - `.github/workflows/corefs-provenance.yml` and `scripts/check_corefs_release_notices.py`
  - `tickets/portable-core-filesystem/` and `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
- Notes:
  - Implementation is validated but remains open until the implementation head and subsequent metadata closeout head each satisfy the repository current-head review stopping rule.
