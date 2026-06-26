# ACT-002 - Scaffold Rust package and license notes

- Status: backlog
- Priority: P1
- Scope: `apps/animus`
- Parent: `ACT-000`
- Depends on: none
- Owner: unassigned
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 03:00 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only

