# Animus Rust Coding TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current Bun/Ink `apps/animus` CLI with a Rust-native ANIMA-first coding terminal.

**Architecture:** Keep ANIMA server-driven. The Rust TUI owns terminal UX, local delegated action tools, permissions, and rendering; `/ws/agent` owns agent turns, memory, model calls, run state, approvals, and ANIMA-native spawn lifecycle. Use typed Rust protocol models and a reducer-style app state so streaming, approvals, tools, and spawns stay testable.

**Tech Stack:** Rust, Tokio, Ratatui/Crossterm or equivalent terminal stack, tokio-tungstenite, Serde, Python/FastAPI server.

**Spec:** `docs/superpowers/specs/2026-06-27-animus-rust-coding-tui-design.md`

**PRD:** `docs/prds/animus/rust-coding-tui-v1.md`

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `apps/animus/Cargo.toml` | Rust package config replacing Bun package entry |
| `apps/animus/src/main.rs` | CLI args, startup, terminal lifecycle |
| `apps/animus/src/config.rs` | Server URL, workspace, token/config loading |
| `apps/animus/src/protocol.rs` | Typed ANIMA WebSocket frames |
| `apps/animus/src/client.rs` | WebSocket auth, reconnect, send queue, frame dispatch |
| `apps/animus/src/app.rs` | App state and reducer-style event handling |
| `apps/animus/src/tui.rs` | Terminal event loop and top-level layout |
| `apps/animus/src/transcript.rs` | Transcript item model and renderers |
| `apps/animus/src/input.rs` | Input buffer, history, key handling, autocomplete |
| `apps/animus/src/commands.rs` | Slash command registry and routing |
| `apps/animus/src/approvals.rs` | Approval models, decisions, and UI rendering |
| `apps/animus/src/tools/mod.rs` | Local action tool registry |
| `apps/animus/src/tools/shell.rs` | Shell execution |
| `apps/animus/src/tools/files.rs` | Read/write/edit/list/search file tools |
| `apps/animus/src/permissions.rs` | Local permission rules |
| `apps/animus/src/spawns.rs` | ANIMA background worker status model/rendering |
| `apps/animus/NOTICE.md` | License/source notes for adapted upstream UI/protocol ideas |
| `apps/server/src/anima_server/api/routes/ws.py` | WebSocket frame handling, approval/cancel fixes |
| `apps/server/tests/test_ws.py` | Server protocol tests |

---

## Reference Alignment Rules

Implementers should mirror the local Rust coding-agent reference architecture in behavior and boundaries:

- Turn lifecycle is explicit: start, active turn id/run id, steer-like continuation, interrupt/cancel, terminal status.
- Raw wire frames are mapped into typed protocol structs, then into app events, then into transcript/history cells.
- The TUI is event-driven: widgets emit app events; the top-level app owns mutation, I/O side effects, and shutdown.
- Transcript output is cell-based, not one generic message blob.
- Approvals use typed decision enums and separate display models for shell execution and file changes.
- Slash commands are registry/enum-driven with descriptions, inline-arg support, ordering, and availability rules.
- Status line items are composable: run state, cwd/project, permissions, approval mode, active run/thread, spawn count, and task progress.
- Background workers use a thread/spawn presentation model with running/closed state, task preview, and navigation/listing.

Do not mirror provider login, account state, cloud/desktop links, model-provider assumptions, or product branding.

---

## Task 1: Server Protocol and Run Lifecycle

**Files:**
- Modify: `apps/server/src/anima_server/api/routes/ws.py`
- Test: `apps/server/tests/test_ws.py`

- [ ] Add/confirm server frames for `run_started`, `cancelled`, `approval_required`, `turn_complete`, structured errors, and spawn lifecycle.
- [ ] Implement or complete server handling for `approval_response`.
- [ ] Make `cancel` require and honor current `run_id`.
- [ ] Make cancel idempotent and emit a terminal user-visible frame.
- [ ] Align server/client lifecycle semantics with typed start/active/interrupt/terminal turn states.
- [ ] Add focused server tests for approval response and cancel frame shape.
- [ ] Run `bun run test` or focused server tests.

## Task 2: Rust Package Scaffold and License Hygiene

**Files:**
- Create/replace: `apps/animus/Cargo.toml`
- Create: `apps/animus/src/main.rs`
- Create: `apps/animus/NOTICE.md`
- Modify: root workspace/build files if needed

- [ ] Replace Bun package wiring with a Rust package for `apps/animus`.
- [ ] Add terminal/WebSocket/serde dependencies.
- [ ] Create minimal `main.rs` that parses args and exits cleanly.
- [ ] Add `NOTICE.md` for Apache-2.0-compatible source adaptation notes.
- [ ] Verify no upstream product names, logos, ASCII art, docs links, cloud/ADE references, or brand assets are copied.
- [ ] Run `cargo check` for the new package.

## Task 3: ANIMA WebSocket Client

**Files:**
- Create: `apps/animus/src/config.rs`
- Create: `apps/animus/src/protocol.rs`
- Create: `apps/animus/src/client.rs`

- [ ] Define typed Rust enums/structs for client and server frames.
- [ ] Split wire protocol structs from app-level events.
- [ ] Implement config loading for server URL, workspace, and unlock token/config.
- [ ] Implement WebSocket connect/auth/tool schema registration.
- [ ] Track current `run_id`.
- [ ] Implement send helpers for `user_message`, `tool_result`, `approval_response`, and `cancel`.
- [ ] Add protocol serialization/deserialization tests.
- [ ] Run `cargo test -p animus` or package-equivalent tests.

## Task 4: Terminal Event Loop and Basic Transcript

**Files:**
- Create: `apps/animus/src/app.rs`
- Create: `apps/animus/src/tui.rs`
- Create: `apps/animus/src/transcript.rs`
- Modify: `apps/animus/src/main.rs`

- [ ] Initialize terminal raw mode/alternate screen and restore it on exit.
- [ ] Add app state for connection, input, transcript, current run, and errors.
- [ ] Add an app event enum so UI widgets request actions without mutating state directly.
- [ ] Render a basic transcript with user, assistant, reasoning, tool, and error entries.
- [ ] Stream assistant tokens into the live transcript.
- [ ] Add app reducer tests for representative server frames.
- [ ] Run Rust tests and manual start/exit smoke test.

## Task 5: Local Tools and Permissions

**Files:**
- Create: `apps/animus/src/tools/mod.rs`
- Create: `apps/animus/src/tools/shell.rs`
- Create: `apps/animus/src/tools/files.rs`
- Create: `apps/animus/src/permissions.rs`

- [ ] Port existing action tool schemas conceptually to Rust.
- [ ] Implement shell execution with timeout/cancel support.
- [ ] Implement read/write/edit/list/search file tools.
- [ ] Implement local permission decisions for read/write/shell tools.
- [ ] Return structured `tool_result` frames to server.
- [ ] Add unit tests for permission decisions and tool dispatch.

## Task 6: Input, Slash Commands, and Status Line

**Files:**
- Create: `apps/animus/src/input.rs`
- Create: `apps/animus/src/commands.rs`
- Modify: `apps/animus/src/tui.rs`
- Modify: `apps/animus/src/app.rs`

- [ ] Add input buffer, cursor movement, history, and multiline handling.
- [ ] Define commands: `/help`, `/clear`, `/cancel`, `/reconnect`, `/permissions`, `/status`, `/diff`, `/spawns`, `/cancel-spawn`, `/quit`.
- [ ] Add enum/registry-driven command metadata: presentation order, description, inline-arg support, busy-state availability, and autocomplete filtering.
- [ ] Render status line with connection, cwd, mode, current run, approval state, and spawn count.
- [ ] Add command parsing/routing tests.

## Task 7: Inline Approvals

**Files:**
- Create: `apps/animus/src/approvals.rs`
- Modify: `apps/animus/src/app.rs`
- Modify: `apps/animus/src/tui.rs`
- Modify: `apps/animus/src/permissions.rs`

- [ ] Model pending approval state separately from transcript state.
- [ ] Render shell/file/question/generic approvals inline.
- [ ] Support accept, accept-for-session, policy-amendment accept where available, decline, and cancel decisions.
- [ ] Keep shell execution and file-change approval display models separate.
- [ ] Send `approval_response` frames with expected IDs.
- [ ] Recover gracefully if an approval disappears after reconnect.
- [ ] Add approval reducer tests.

## Task 8: ANIMA Spawn/Thread Visibility

**Files:**
- Create: `apps/animus/src/spawns.rs`
- Modify: `apps/animus/src/protocol.rs`
- Modify: `apps/animus/src/tui.rs`
- Modify: `apps/animus/src/commands.rs`
- Reference: `docs/prds/three-tier-architecture/P8-n-agent-spawning.md`

- [ ] Add spawn event types for queued/running/completed/failed/cancelled.
- [ ] Render running spawn count in the status line.
- [ ] Add `/spawns` list view.
- [ ] Add `/cancel-spawn <id>` when server support exists.
- [ ] Include task preview, running/closed state, and list navigation in the spawn presentation model.
- [ ] Present spawned workers as ANIMA background processes, not independent personas.
- [ ] Add mocked spawn event rendering tests.

## Task 9: Replace Bun Wiring, Docs, and Smoke Tests

**Files:**
- Remove/replace: `apps/animus/package.json`
- Remove/replace: `apps/animus/tsconfig.json`
- Modify: root package/build scripts as needed
- Modify: docs and tickets

- [ ] Remove Bun/Ink package wiring for `apps/animus`.
- [ ] Update root scripts so `bun run build` and relevant dev commands build the Rust Animus package.
- [ ] Run `cargo test` for Animus.
- [ ] Run `bun run build`.
- [ ] Run `bun run test`.
- [ ] Smoke-test `/health`.
- [ ] Smoke-test Animus prompt -> stream -> delegated tool -> approval -> completion.
- [ ] Smoke-test cancel/reconnect.
- [ ] Smoke-test `/spawns` with mocked or real spawn events.
- [ ] Update tickets with validation and changed paths.
