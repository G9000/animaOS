# ACT-002 - Scaffold Rust package and license notes

- Status: done
- Priority: P1
- Scope: `apps/animus`
- Parent: `ACT-000`
- Depends on: none
- Owner: codex
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 04:36 MYT
- Started: 2026-06-27 04:34 MYT
- Completed: 2026-06-27 04:36 MYT

## Goal

Replace the current Bun package shell with a Rust package scaffold for Animus.

## Deliverables

- `apps/animus/Cargo.toml`.
- Minimal `apps/animus/src/main.rs`.
- Rust dependencies for terminal UI, async runtime, WebSocket, and serialization.
- `apps/animus/NOTICE.md` for source adaptation/license notes.
- No upstream brand assets or product names copied into Animus.

## Acceptance

- `cargo check` can compile the empty/minimal Animus Rust package.
- Bun/Ink source remains only until later replacement tasks remove it.
- License/source adaptation rules are documented before source adaptation begins.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.
- 2026-06-27 03:00 MYT - Revised for Rust package scaffold.
- 2026-06-27 04:34 MYT - Started Rust package scaffold with headless CLI tests first.
- 2026-06-27 04:36 MYT - Completed Rust package scaffold, headless CLI summary, and source notice.

## Validation

- Commands:
  - `cargo test -p animus` - red first: missing `Cli` and `startup_summary`
  - `cargo test -p animus` - passed: 2 passed
  - `cargo check -p animus` - passed
  - `cargo run -p animus -- --headless` - passed; printed startup summary without secrets
- Changed paths:
  - Cargo.toml
  - apps/animus/Cargo.toml
  - apps/animus/src/main.rs
  - apps/animus/NOTICE.md
- Notes:
  - Legacy Bun/Ink files remain for later replacement in ACT-009.
  - No Codex source files have been adapted yet.

