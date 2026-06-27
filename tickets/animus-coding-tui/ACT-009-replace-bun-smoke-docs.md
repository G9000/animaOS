# ACT-009 - Replace Bun wiring, smoke tests, and docs

- Status: done
- Priority: P1
- Scope: `apps/animus`, `apps/server`, `docs`, `tickets`
- Parent: `ACT-000`
- Depends on: `ACT-005`, `ACT-006`, `ACT-007`, `ACT-008`
- Owner: codex
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-27 03:00 MYT
- Updated: 2026-06-27 11:38 MYT
- Started: 2026-06-27 06:31 MYT
- Completed: 2026-06-27 11:38 MYT

## Goal

Remove Bun/Ink support wiring, validate the Rust replacement, and update docs/tracker state.

## Deliverables

- Bun/Ink package wiring removed or replaced.
- Root build/dev scripts updated for Rust Animus.
- Build/test/smoke validation recorded.
- Usage docs updated.
- Parent and child tickets updated with validation.

## Acceptance

- `cargo test` result is recorded.
- `bun run build` result is recorded.
- `bun run test` result is recorded.
- `/health` smoke check result is recorded.
- Rust Animus can complete a representative coding turn through ANIMA.
- Cancel/reconnect and `/spawns` are smoke-tested.

## Activity Log

- 2026-06-27 03:00 MYT - Ticket created for Rust replacement closure.
- 2026-06-27 06:31 MYT - Moved to in_progress for final Rust wiring, Bun/Ink removal, docs, and smoke validation.
- 2026-06-27 11:38 MYT - Completed Rust-only wiring, removed legacy TypeScript package files, refreshed docs/lockfile, fixed validation blockers, and ran final smoke/build/test checks.

## Validation

- Commands:
  - `bun install --frozen-lockfile` - passed
  - `git diff --check` - passed with Windows line-ending warnings only
  - `cargo test -p animus` - passed: 51 passed
  - `cargo check -p animus` - passed
  - `bun run test:animus` - passed: 51 passed
  - `cargo run -p animus -- --headless` - passed; printed Rust startup summary
  - `bun run build` - passed for server, desktop, and `cargo check -p animus`
  - `bun run test` - blocked before collection without a Core encryption passphrase
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - passed: 1647 passed, 1 skipped, 235 warnings
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_creation_flow.py::test_agent_can_generate_thinking_monologue_draft -q` - passed
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_today_context.py -q` - passed: 8 passed
  - `GET /health` against `bun run dev:server` from this worktree with `ANIMA_CORE_REQUIRE_ENCRYPTION=false` - HTTP 200, `{"status":"ok","service":"server","environment":"development","provisioned":false}`
- Changed paths:
  - apps/animus/Cargo.toml
  - apps/animus/package.json
  - apps/animus/tsconfig.json
  - apps/animus/src/**/*.ts
  - apps/animus/src/**/*.tsx
  - apps/animus/src/client.rs
  - apps/animus/src/tui.rs
  - apps/server/src/anima_server/schemas/chat.py
  - apps/server/src/anima_server/services/agent/thinking_monologue.py
  - bun.lock
  - package.json
  - docs/prds/animus/rust-coding-tui-v1.md
  - docs/superpowers/specs/2026-06-27-animus-rust-coding-tui-design.md
  - tickets/animus-coding-tui/ACT-000-parent.md
  - tickets/animus-coding-tui/ACT-009-replace-bun-smoke-docs.md
- Notes:
  - Removed 61 legacy Animus TypeScript/package files and two empty legacy directories.
  - Root `dev:animus`, `test:animus`, and `build:animus` now route through Cargo; root `build` includes the Animus Rust check.
  - Rust smoke coverage includes prompt outbound, cancel/reconnect outbound, approval response outbound, delegated tool-result execution, spawn rendering, and `/spawns` command tests.
  - Server websocket smoke coverage is included in the passing full suite via `apps/server/tests/test_ws.py`.
  - Fixed two validation blockers discovered by the final suite: duplicate `TodayContext` schema definition and raw HTTP provider errors escaping thinking-monologue fallback.
  - No database schema changes; Alembic was not run.

