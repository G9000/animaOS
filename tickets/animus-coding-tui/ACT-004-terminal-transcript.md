# ACT-004 - Add terminal event loop and transcript

- Status: done
- Priority: P1
- Scope: `apps/animus`
- Parent: `ACT-000`
- Depends on: `ACT-003`
- Owner: codex
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 04:48 MYT
- Started: 2026-06-27 04:43 MYT
- Completed: 2026-06-27 04:48 MYT

## Goal

Build the Rust terminal event loop and basic transcript rendering.

## Deliverables

- Terminal raw mode/alternate screen lifecycle.
- App state and reducer for connection, input, transcript, current run, and errors.
- App event enum for widget-to-app coordination.
- History-cell renderers for user, assistant, reasoning, shell execution, file changes, plans/todos, approvals, notices, search, session events, and errors.
- Live streaming assistant output.

## Acceptance

- Terminal state is restored on normal exit and error exit.
- Mocked server frames render through typed app events into readable history cells.
- App reducer tests cover representative stream frames.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.
- 2026-06-27 03:00 MYT - Revised for Rust terminal transcript.
- 2026-06-27 04:43 MYT - Started reducer, transcript, and TUI rendering tests.
- 2026-06-27 04:48 MYT - Completed app reducer, transcript renderers, terminal guard/event loop, and headless/non-headless wiring.

## Validation

- Commands:
  - `cargo test -p animus` - red first: missing `AppState`, `TranscriptItem`, and `render_to_text`
  - `cargo test -p animus app` - passed: 5 passed, 15 filtered out
  - `cargo test -p animus transcript` - passed: 4 passed, 16 filtered out
  - `cargo test -p animus tui` - passed: 1 passed, 19 filtered out
  - `cargo test -p animus` - passed: 20 passed
  - `cargo check -p animus` - passed
  - `cargo run -p animus -- --headless` - passed; printed startup summary
- Changed paths:
  - apps/animus/src/app.rs
  - apps/animus/src/tui.rs
  - apps/animus/src/transcript.rs
  - apps/animus/src/main.rs
- Notes:
  - `cargo test -p animus app transcript tui` is not valid Cargo syntax; focused filters were run separately.
  - Terminal setup uses a drop guard to restore raw mode and alternate screen.
  - No Codex source files were adapted.
