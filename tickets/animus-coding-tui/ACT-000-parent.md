# ACT-000 - Animus Rust Coding TUI Parent Tracker

- Status: done
- Priority: P1
- Scope: `apps/animus`, `apps/server`, `docs/prds/animus`
- Depends on: none
- Owner: codex
- PRD: docs/prds/animus/rust-coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-27 21:09 MYT
- Started: 2026-06-27 04:10 MYT
- Completed: 2026-06-27 11:38 MYT

## Goal

Track the rewrite that replaces the current Bun/Ink Animus CLI with a Rust-native ANIMA-first coding terminal.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `ACT-001` | Fix server protocol and run lifecycle | `done` | none |
| `ACT-002` | Scaffold Rust package and license notes | `done` | none |
| `ACT-003` | Build ANIMA WebSocket client | `done` | `ACT-001`, `ACT-002` |
| `ACT-004` | Add terminal event loop and transcript | `done` | `ACT-003` |
| `ACT-005` | Add local tools and permissions | `done` | `ACT-003` |
| `ACT-006` | Add input, slash commands, and status line | `done` | `ACT-004` |
| `ACT-007` | Add inline approvals | `done` | `ACT-001`, `ACT-004`, `ACT-005` |
| `ACT-008` | Add ANIMA spawn/thread visibility | `done` | `ACT-004` |
| `ACT-009` | Replace Bun wiring, smoke tests, and docs | `done` | `ACT-005`, `ACT-006`, `ACT-007`, `ACT-008` |

## Deliverables

- Rust-native Animus TUI replacing the Bun/Ink implementation.
- Explicit protocol-first, event-driven, history-cell-based Rust TUI architecture.
- Fixed ANIMA WebSocket protocol lifecycle for runs, cancel, and approvals.
- Rich transcript, tool rendering, input, command autocomplete, and status display.
- Local Rust action tools and permission checks.
- ANIMA-native background spawn/thread visibility and commands.
- License/source hygiene for adapted upstream UI/protocol ideas.

## Acceptance

- Every child ticket references this parent.
- Parent status table reflects child progress.
- Completed child tickets are listed below with timestamps.
- Initiative can be picked up from the PRD, plan, and ticket folder without prior chat context.
- The v1 replacement does not retain the legacy Bun/Ink CLI.

## Completed Tickets

- 2026-06-27 04:33 MYT - `ACT-001` fixed websocket approval/cancel lifecycle and protocol frame translation.
- 2026-06-27 04:36 MYT - `ACT-002` added the Rust Animus workspace package, headless CLI scaffold, and source notice.
- 2026-06-27 04:42 MYT - `ACT-003` added typed Rust protocol/config/websocket client foundations.
- 2026-06-27 04:48 MYT - `ACT-004` added app reducer, transcript renderers, and terminal event loop.
- 2026-06-27 04:55 MYT - `ACT-005` added local tools, permission checks, and tool-result dispatch.
- 2026-06-27 04:59 MYT - `ACT-006` added input buffer, slash commands, command routing, and status line.
- 2026-06-27 05:03 MYT - `ACT-007` added inline approval state, decision frames, and TUI prompts.
- 2026-06-27 06:20 MYT - `ACT-008` added background spawn visibility and typed spawn statuses.
- 2026-06-27 11:38 MYT - `ACT-009` replaced Bun wiring with Rust/Cargo scripts, removed legacy TypeScript files, refreshed docs, and completed final validation.

## Activity Log

- 2026-06-26 18:51 MYT - Parent tracker created for Animus coding TUI work.
- 2026-06-27 03:00 MYT - Revised initiative scope to a full Rust-native rewrite replacing Bun/Ink.
- 2026-06-27 04:10 MYT - Planning branch created; implementation plan refined with concrete ANIMA files, Codex reference paths, protocol shape, test commands, and execution rules.
- 2026-06-27 04:20 MYT - Ran planning validation; build and health passed, full backend tests reported unrelated/environment-sensitive failures.
- 2026-06-27 04:28 MYT - ACT-001 moved to in_progress for websocket lifecycle implementation.
- 2026-06-27 04:33 MYT - ACT-001 completed and verified.
- 2026-06-27 04:34 MYT - ACT-002 moved to in_progress for Rust package scaffold.
- 2026-06-27 04:36 MYT - ACT-002 completed and verified.
- 2026-06-27 04:37 MYT - ACT-003 moved to in_progress for Rust protocol and websocket client.
- 2026-06-27 04:42 MYT - ACT-003 completed and verified.
- 2026-06-27 04:43 MYT - ACT-004 moved to in_progress for reducer and terminal transcript work.
- 2026-06-27 04:48 MYT - ACT-004 completed and verified.
- 2026-06-27 04:49 MYT - ACT-005 moved to in_progress for local tools and permissions.
- 2026-06-27 04:55 MYT - ACT-005 completed and verified.
- 2026-06-27 04:55 MYT - ACT-006 moved to in_progress for input, commands, and status line.
- 2026-06-27 04:59 MYT - ACT-006 completed and verified.
- 2026-06-27 05:00 MYT - ACT-007 moved to in_progress for inline approval flow.
- 2026-06-27 05:03 MYT - ACT-007 completed and verified.
- 2026-06-27 05:04 MYT - ACT-008 moved to in_progress for background spawn visibility.
- 2026-06-27 06:20 MYT - ACT-008 completed and verified.
- 2026-06-27 06:31 MYT - ACT-009 moved to in_progress for final replacement wiring and validation.
- 2026-06-27 11:38 MYT - ACT-009 completed; all child tickets are done and the parent tracker is complete.
- 2026-06-27 12:10 MYT - Codex review blockers were fixed and final lint/build/test validation was rerun for the PR branch.
- 2026-06-27 12:43 MYT - Second Codex review round was fixed and revalidated.
- 2026-06-27 13:02 MYT - Third Codex review round was fixed and revalidated.
- 2026-06-27 13:24 MYT - Fourth Codex review round was fixed and revalidated.
- 2026-06-27 16:52 MYT - Fifth Codex review round was fixed and revalidated.
- 2026-06-27 17:20 MYT - Sixth Codex review round was fixed and revalidated.
- 2026-06-27 18:22 MYT - Seventh Codex review round was fixed and revalidated.
- 2026-06-27 20:35 MYT - Eighth Codex review round was fixed and revalidated.
- 2026-06-27 21:09 MYT - Ninth Codex review round was fixed and revalidated.

## Validation

- Commands:
  - `bun install --frozen-lockfile` - passed
  - `bun run build` - passed for `server` and `desktop`
  - `git diff --check` - passed with Windows line-ending warnings
  - `bun run test` - failed before collection until `ANIMA_CORE_REQUIRE_ENCRYPTION=false` was set
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - timed out after 301s with 4 reported failures, 1638 passed, 1 skipped
  - `GET /health` against a dev server started from this worktree - HTTP 200
  - `bun install --frozen-lockfile` - passed after removing the Animus Bun package
  - `git diff --check` - passed with Windows line-ending warnings only
  - `cargo test -p animus` - passed: 51 passed
  - `cargo check -p animus` - passed
  - `bun run test:animus` - passed: 51 passed
  - `cargo run -p animus -- --headless` - passed
  - `bun run build` - passed for server, desktop, and `cargo check -p animus`
  - `bun run test` - blocked before collection without a Core encryption passphrase
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - passed: 1647 passed, 1 skipped, 235 warnings
  - `GET /health` against `bun run dev:server` from this worktree with `ANIMA_CORE_REQUIRE_ENCRYPTION=false` - HTTP 200
  - `cargo test -p animus` - passed after review fixes: 57 passed
  - `cargo check -p animus` - passed after review fixes
  - `bun run lint` - passed for server and desktop
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - passed after review fixes: 1647 passed, 1 skipped, 235 warnings
  - `bun run build` - passed after review fixes for server, desktop, and `cargo check -p animus`
  - `cargo test -p animus` - passed after second review fixes: 63 passed
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_ws.py -q` - passed after second review fixes: 10 passed
  - `bun run lint` - passed after second review fixes for server and desktop
  - `bun run build` - passed after second review fixes for server, desktop, and `cargo check -p animus`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - passed after second review fixes: 1648 passed, 1 skipped, 235 warnings
  - `git diff --check` - passed after third review fixes with Windows line-ending warnings only
  - `bun run test:animus` - passed after third review fixes: 66 passed
  - `bun run lint` - passed after third review fixes for server and desktop
  - `bun run build` - passed after third review fixes for server, desktop, and `cargo check -p animus`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - passed after third review fixes: 1648 passed, 1 skipped, 235 warnings
  - `git diff --check` - passed after fourth review fixes with Windows line-ending warnings only
  - `bun run test:animus` - passed after fourth review fixes: 71 passed
  - `bun run lint` - passed after fourth review fixes for server and desktop
  - `bun run build` - passed after fourth review fixes for server, desktop, and `cargo check -p animus`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - passed after fourth review fixes: 1648 passed, 1 skipped, 235 warnings
  - `cargo test -p animus` - passed after fifth review fixes: 74 passed
  - `git diff --check` - passed after fifth review fixes with Windows line-ending warnings only
  - `bun run test:animus` - passed after fifth review fixes: 74 passed
  - `bun run lint` - passed after fifth review fixes for server and desktop
  - `bun run build` - passed after fifth review fixes for server, desktop, and `cargo check -p animus`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - passed after fifth review fixes: 1648 passed, 1 skipped, 235 warnings
  - `cargo test -p animus shell_exec_truncates_large_output_before_returning_tool_result` - failed before the shell cap with 1005 stdout lines, passed after the fix
  - `cargo test -p animus render_text_keeps_newest_transcript_rows_visible` - failed before tail rendering hid `message-9`, passed after the fix
  - `cargo test -p animus` - passed after sixth review fixes: 76 passed
  - `git diff --check` - passed after sixth review fixes with Windows line-ending warnings only
  - `bun run test:animus` - passed after sixth review fixes: 76 passed
  - `bun run lint` - passed after sixth review fixes for server and desktop
  - `bun run build` - passed after sixth review fixes for server, desktop, and `cargo check -p animus`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - passed after sixth review fixes: 1648 passed, 1 skipped, 235 warnings
  - `cargo test -p animus glob_honors_limit_and_reports_truncation` - failed before the glob cap returned `src/file-2.rs`, passed after the fix
  - `cargo metadata --locked --offline --format-version 1` - passed after committing the root Cargo lockfile
  - `cargo test -p animus` - passed after seventh review fixes: 77 passed
  - `git diff --check` - passed after seventh review fixes with Windows line-ending warnings only
  - `bun run test:animus` - passed after seventh review fixes: 77 passed
  - `bun run lint` - passed after seventh review fixes for server and desktop
  - `bun run build` - passed after seventh review fixes for server, desktop, and `cargo check -p animus`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - passed after seventh review fixes: 1648 passed, 1 skipped, 235 warnings
  - `cargo test -p animus workspace_write_denies_dangling_symlink_to_outside_target -- --nocapture` - failed before the dangling-symlink resolver fix, passed after the fix
  - `cargo fmt -p animus --check` - passed after eighth review fixes
  - `cargo test -p animus` - passed after eighth review fixes: 78 passed
  - `git diff --check` - passed after eighth review fixes with Windows line-ending warnings only
  - `cargo metadata --locked --offline --format-version 1` - passed after eighth review fixes
  - `bun run test:animus` - passed after eighth review fixes: 78 passed
  - `bun run lint` - passed after eighth review fixes for server and desktop
  - `bun run build` - passed after eighth review fixes for server, desktop, and `cargo check -p animus`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - passed after eighth review fixes: 1648 passed, 1 skipped, 235 warnings
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_ws.py::TestWebSocketRunHandlers::test_ws_agent_rejects_user_message_while_run_awaits_approval -q` - failed before the awaiting-approval busy guard by accepting a second turn, passed after the fix
  - `cargo test -p animus terminal_agent_error_clears_active_run -- --nocapture` - failed before terminal error run clearing, passed after the fix
  - `cargo test -p animus clears_current_run_id_after_terminal_agent_error -- --nocapture` - failed before terminal error run clearing, passed after the fix
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test:server -- apps/server/tests/test_ws.py -q` - passed after ninth review fixes: 11 passed
  - `cargo fmt -p animus --check` - passed after ninth review fixes
  - `cargo test -p animus` - passed after ninth review fixes: 80 passed
  - `git diff --check` - passed after ninth review fixes with Windows line-ending warnings only
  - `cargo metadata --locked --offline --format-version 1` - passed after ninth review fixes
  - `bun run test:animus` - passed after ninth review fixes: 80 passed
  - `bun run lint` - passed after ninth review fixes for server and desktop
  - `bun run build` - passed after ninth review fixes for server, desktop, and `cargo check -p animus`
  - `$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test` - passed after ninth review fixes: 1649 passed, 1 skipped, 235 warnings
- Changed paths:
  - tickets/animus-coding-tui/ACT-000-parent.md
  - docs/superpowers/plans/2026-06-27-animus-rust-coding-tui.md
  - docs/superpowers/specs/2026-06-27-animus-rust-coding-tui-design.md
  - package.json
  - bun.lock
  - apps/animus
  - apps/server/src/anima_server/schemas/chat.py
  - apps/server/src/anima_server/services/agent/thinking_monologue.py
  - docs/prds/animus/rust-coding-tui-v1.md
  - tickets/animus-coding-tui/ACT-009-replace-bun-smoke-docs.md
  - apps/server/src/anima_server/api/routes/auth.py
  - apps/server/src/anima_server/api/routes/users.py
  - apps/server/src/anima_server/api/routes/ws.py
  - apps/server/tests/test_agent_biography_preview.py
  - apps/server/tests/test_agent_service.py
  - apps/server/tests/test_ws.py
- Notes:
  - planning-only update; implementation child tickets remain backlog
  - full-suite failures reported before timeout: `test_bm25_search_uses_rust_memory_index_when_clean`, `test_agent_can_generate_thinking_monologue_draft`, and two `test_accepts_plus_or_minus_one_day_for_timezone_skew` cases
  - final full-suite validation passed after fixing the duplicate `TodayContext` class and thinking-monologue HTTP fallback
  - Codex review fixes applied stricter workspace path containment, live permission policy updates, visible approval controls, active input-buffer wiring, and shell timeout cleanup
  - second Codex review fixes handled unique edit matching, path-qualified globs, approve-for-session reuse, ask_user schema exposure, and websocket approval resume concurrency
  - third Codex review fixes handled reconnect-time outbound frame queueing, background process cleanup on registry drop, and bounded background output buffering
  - fourth Codex review fixes handled delegated shell permission gating, async local tool execution, shell/background output redaction, and grep result caps
  - fifth Codex review fixes handled CRLF-normalized edit matching and progressive `multi_edit` validation
  - sixth Codex review fixes handled immediate shell output caps and newest-transcript-row rendering
  - seventh Codex review fixes handled root Cargo lockfile reproducibility and `glob` result limits
  - eighth Codex review fixes handled dangling symlink rejection before workspace-write approval
  - ninth Codex review fixes handled awaiting-approval turn blocking and terminal error run cleanup
  - no database schema changes; Alembic was not run
