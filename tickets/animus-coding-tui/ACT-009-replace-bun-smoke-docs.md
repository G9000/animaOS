# ACT-009 - Replace Bun wiring, smoke tests, and docs

- Status: backlog
- Priority: P1
- Scope: `apps/animus`, `docs`, `tickets`
- Parent: `ACT-000`
- Depends on: `ACT-005`, `ACT-006`, `ACT-007`, `ACT-008`
- Owner: unassigned
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-27 03:00 MYT
- Updated: 2026-06-27 03:00 MYT
- Started:
- Completed:

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

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only

