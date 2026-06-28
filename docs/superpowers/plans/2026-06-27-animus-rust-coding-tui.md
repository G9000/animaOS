# Animus Rust Coding TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current Bun/Ink `apps/animus` CLI with a Rust-native ANIMA-first coding terminal.

**Architecture:** Keep ANIMA server-driven. The Rust TUI owns terminal UX, local delegated action tools, permissions, and rendering; `/ws/agent` owns agent turns, memory, model calls, run state, approvals, and ANIMA-native spawn lifecycle. Use typed Rust protocol models and reducer-style app state so streaming, approvals, tools, and spawns stay testable.

**Tech Stack:** Rust, Tokio, Ratatui, Crossterm, tokio-tungstenite, Serde, Python/FastAPI, SQLAlchemy runtime models.

**Spec:** `docs/superpowers/specs/2026-06-27-animus-rust-coding-tui-design.md`

**PRD:** `docs/prds/animus/rust-coding-tui-v1.md`

**Parent Ticket:** `tickets/animus-coding-tui/ACT-000-parent.md`

---

## Execution Rules

- Work in an isolated worktree and branch. Do not implement this on `main`.
- Execute tickets in order unless a dependency explicitly allows parallel work.
- Open each ticket before working it, set `Status: in_progress`, set `Started:` if empty, update `Updated:`, and append an `Activity Log` entry using `YYYY-MM-DD HH:MM MYT`.
- Commit after each completed ticket or after each coherent dependency slice.
- Keep ANIMA as the backend and identity owner. Animus is a local terminal shell around `/ws/agent`, not a separate agent runtime.
- Copy or adapt only generic Apache-2.0-compatible code from `C:\Users\leoca\OneDrive\Desktop\anima\codex`; record adapted source paths in `apps/animus/NOTICE.md`.
- Do not copy Codex product names, logos, ASCII art, cloud/account/provider flows, ChatGPT/OpenAI-specific auth, or model-provider assumptions.
- Keep spawned workers as ANIMA background processes under one identity. Do not present spawned workers as independent user-facing personas.

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
| `apps/animus/src/tools/process.rs` | Background process start/output/stop/list tools |
| `apps/animus/src/permissions.rs` | Local permission rules |
| `apps/animus/src/spawns.rs` | ANIMA background worker status model/rendering |
| `apps/animus/NOTICE.md` | License/source notes for adapted upstream UI/protocol ideas |
| `apps/server/src/anima_server/api/routes/ws.py` | WebSocket frame handling, approval/cancel fixes |
| `apps/server/tests/test_ws.py` | Focused WebSocket route/protocol tests |
| `Cargo.toml` | Add `apps/animus` to the Rust workspace |
| `package.json` | Replace Bun Animus scripts with Rust-compatible commands |
| `tickets/animus-coding-tui/*.md` | Ticket status, validation, changed paths |

---

## Codex Reference Source Map

Use the local Codex checkout as a reference, not as a wholesale vendor drop.

| Animus Area | Codex Reference Files | Adaptation Notes |
| --- | --- | --- |
| Terminal lifecycle and event loop | `codex-rs/tui/src/tui.rs`, `codex-rs/tui/src/tui/event_stream.rs`, `codex-rs/tui/src/tui/frame_requester.rs` | Adapt raw-mode/alternate-screen cleanup, crossterm event streaming, frame coalescing. Keep ANIMA app events and config. |
| App event routing | `codex-rs/tui/src/app_event.rs`, `codex-rs/tui/src/app_event_sender.rs`, `codex-rs/tui/src/app.rs` | Copy the event-bus shape conceptually. Do not port Codex app-server session lifecycle. |
| History cells/transcript | `codex-rs/tui/src/history_cell/*.rs`, `codex-rs/tui/src/thread_transcript.rs`, `codex-rs/tui/src/streaming/mod.rs` | Adapt cell-based rendering for ANIMA events: user, assistant, reasoning, shell, file edit, approval, spawn, error. |
| Input/composer | `codex-rs/tui/src/bottom_pane/textarea.rs`, `codex-rs/tui/src/bottom_pane/chat_composer*.rs`, `codex-rs/tui/src/insert_history.rs` | Use editor/history/key-handling patterns. Keep the first Animus version smaller than Codex composer. |
| Slash commands | `codex-rs/tui/src/slash_command.rs`, `codex-rs/tui/src/bottom_pane/slash_commands.rs`, `codex-rs/tui/src/bottom_pane/command_popup.rs` | Adapt enum/registry metadata, filtering, inline args, and availability rules. Use ANIMA commands only. |
| Approvals | `codex-rs/tui/src/approval_events.rs`, `codex-rs/tui/src/bottom_pane/approval_overlay.rs`, `codex-rs/tui/src/history_cell/approvals.rs`, `codex-rs/tui/src/app_server_approval_conversions.rs` | Adapt decision modeling and display separation for shell/file approvals. Wire to ANIMA `approval_response`. |
| Status line/footer | `codex-rs/tui/src/status/*`, `codex-rs/tui/src/bottom_pane/footer.rs`, `codex-rs/tui/src/status_indicator_widget.rs`, `codex-rs/tui/src/terminal_title.rs` | Adapt composable status items: connection, cwd, permission mode, approval mode, run id, spawn count. |
| Tool rendering | `codex-rs/tui/src/exec_cell/*`, `codex-rs/tui/src/exec_command.rs`, `codex-rs/tui/src/diff_render.rs` | Adapt shell/file display models. Do not port sandbox or provider-specific execution policy wholesale. |
| Background workers | `codex-rs/tui/src/multi_agents.rs`, `codex-rs/tui/src/app/agent_navigation.rs`, `codex-rs/tui/src/app/loaded_threads.rs` | Translate thread/agent picker concepts into ANIMA spawn visibility. Avoid multi-persona language. |
| Protocol tests | `codex-rs/app-server/tests/suite/v2/turn_start.rs`, `turn_interrupt.rs`, `request_permissions.rs` | Use as lifecycle test inspiration only. Server remains Python/FastAPI. |

---

## Protocol Shape

Rust protocol types should cover every frame used by the server route:

Server to client:

```rust
#[serde(tag = "type")]
enum ServerFrame {
    #[serde(rename = "auth_ok")]
    AuthOk { user: AuthUser },
    #[serde(rename = "run_started")]
    RunStarted { run_id: i64, thread_id: Option<i64> },
    #[serde(rename = "stream_token")]
    StreamToken { token: String },
    #[serde(rename = "reasoning")]
    Reasoning { content: String },
    #[serde(rename = "tool_execute")]
    ToolExecute { tool_call_id: String, tool_name: String, args: serde_json::Value },
    #[serde(rename = "tool_call")]
    ToolCall { tool_call_id: String, tool_name: String, args: serde_json::Value },
    #[serde(rename = "tool_return")]
    ToolReturn { tool_call_id: String, tool_name: String, result: String, is_error: Option<bool> },
    #[serde(rename = "approval_required")]
    ApprovalRequired { run_id: i64, tool_call_id: String, tool_name: String, args: serde_json::Value },
    #[serde(rename = "cancelled")]
    Cancelled { run_id: i64 },
    #[serde(rename = "turn_complete")]
    TurnComplete { response: String, model: String, provider: String, tools_used: Vec<String> },
    #[serde(rename = "spawn_event")]
    SpawnEvent { spawn: SpawnFrame },
    #[serde(rename = "error")]
    Error { message: String, code: String },
}
```

Client to server:

```rust
#[serde(tag = "type")]
enum ClientFrame {
    #[serde(rename = "auth")]
    Auth { unlock_token: Option<String>, username: Option<String>, password: Option<String> },
    #[serde(rename = "tool_schemas")]
    ToolSchemas { tools: Vec<ToolSchema> },
    #[serde(rename = "user_message")]
    UserMessage { message: String },
    #[serde(rename = "tool_result")]
    ToolResult { tool_call_id: String, status: ToolStatus, result: String, stdout: Option<Vec<String>>, stderr: Option<Vec<String>> },
    #[serde(rename = "approval_response")]
    ApprovalResponse { run_id: i64, tool_call_id: String, approved: bool, reason: Option<String> },
    #[serde(rename = "cancel")]
    Cancel { run_id: i64 },
}
```

The exact Rust code can differ, but keep `serde` names compatible with the Python route and the old TypeScript protocol in `apps/animus/src/client/protocol.ts`.

---

## Task 1: Server Protocol and Run Lifecycle

**Ticket:** `tickets/animus-coding-tui/ACT-001-protocol-run-lifecycle.md`

**Files:**
- Modify: `apps/server/src/anima_server/api/routes/ws.py`
- Create/modify: `apps/server/tests/test_ws.py`
- Reference: `apps/server/src/anima_server/services/agent/service.py`
- Reference: `apps/server/src/anima_server/services/agent/streaming.py`
- Reference: `apps/server/tests/test_approval_reentry.py`
- Reference: `apps/server/tests/test_agent_service.py`

- [ ] **Step 1: Open and start the ticket**

Update `ACT-001` metadata: `Status: in_progress`, `Started:` if empty, `Updated:`, and activity log.

- [ ] **Step 2: Write failing WebSocket translation tests**

Add tests that lock expected frame shapes:

```python
def test_translate_event_maps_approval_pending_to_approval_required():
    event = build_approval_pending_event(
        run_id=42,
        tool_name="bash",
        tool_call_id="call-1",
        tool_arguments={"command": "git status"},
    )

    assert ws._translate_event(event) == {
        "type": "approval_required",
        "run_id": 42,
        "tool_call_id": "call-1",
        "tool_name": "bash",
        "args": {"command": "git status"},
    }
```

Also cover `run_started`, `cancelled`, `turn_complete`, `tool_return.is_error`, and structured `error`.

- [ ] **Step 3: Write failing handler tests for approval and cancel**

Test `_handle_approval_response()` with monkeypatched `stream_approve_or_deny()` and a fake websocket connection. It should stream translated resume events back over the same websocket.

Test `_handle_cancel()` with a valid run id and monkeypatched `cancel_agent_run()`; it should send a `cancelled` frame after a successful idempotent cancel.

- [ ] **Step 4: Run focused tests and confirm failure**

Run:

```bash
bun run test:server -- apps/server/tests/test_ws.py -q
```

Expected: tests fail because `approval_pending` is dropped and `_handle_approval_response()` is a stub.

- [ ] **Step 5: Implement WebSocket protocol fixes**

In `ws.py`:
- Map `approval_pending` to `approval_required`.
- Implement `_handle_approval_response()` by validating `run_id`, opening user/runtime sessions, calling `stream_approve_or_deny()`, translating each public event, and sending it over the websocket.
- Send structured error frames for bad request, forbidden/not found, conflict/not awaiting approval, and internal failure.
- Make `_handle_cancel()` send a `cancelled` frame when `cancel_agent_run()` returns a run.
- Preserve HTTP approval/cancel semantics in `routes/chat.py`; do not fork business logic.

- [ ] **Step 6: Run focused and related server tests**

Run:

```bash
bun run test:server -- apps/server/tests/test_ws.py apps/server/tests/test_approval_reentry.py apps/server/tests/test_agent_service.py -q
```

Expected: pass.

- [ ] **Step 7: Complete ticket and commit**

Update `ACT-001` validation and changed paths, set status to `done`, update parent tracker, then commit:

```bash
git add apps/server/src/anima_server/api/routes/ws.py apps/server/tests/test_ws.py tickets/animus-coding-tui/ACT-001-protocol-run-lifecycle.md tickets/animus-coding-tui/ACT-000-parent.md
git commit -m "server: fix animus websocket lifecycle"
```

---

## Task 2: Rust Package Scaffold and License Hygiene

**Ticket:** `tickets/animus-coding-tui/ACT-002-rust-crate-scaffold-license.md`

**Files:**
- Create: `apps/animus/Cargo.toml`
- Create: `apps/animus/src/main.rs`
- Create: `apps/animus/NOTICE.md`
- Modify: `Cargo.toml`
- Keep for now: `apps/animus/package.json`, `apps/animus/tsconfig.json`, old TypeScript sources

- [ ] **Step 1: Start the ticket**

Update ticket metadata and parent activity.

- [ ] **Step 2: Add Rust workspace member**

Modify root `Cargo.toml`:

```toml
members = [
    "packages/anima-core",
    "apps/desktop/src-tauri",
    "apps/animus",
]
```

- [ ] **Step 3: Create `apps/animus/Cargo.toml`**

Use direct crates for the first scaffold:

```toml
[package]
name = "animus"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true
repository.workspace = true

[dependencies]
anyhow = "1.0"
clap = { version = "4.5", features = ["derive"] }
crossterm = { version = "0.28", features = ["event-stream"] }
ratatui = "0.29"
serde.workspace = true
serde_json.workspace = true
thiserror.workspace = true
tokio = { version = "1.42", features = ["macros", "rt-multi-thread", "signal", "time", "process", "io-util"] }
tokio-tungstenite = { version = "0.24", features = ["rustls-tls-native-roots"] }
futures-util = "0.3"
url = "2.5"
uuid.workspace = true
chrono.workspace = true
```

- [ ] **Step 4: Create minimal CLI entrypoint**

`main.rs` should parse `--server-url`, `--workspace`, `--token`, `--username`, `--password`, and `--headless` placeholders, print a non-TUI startup summary in headless mode, and exit cleanly.

- [ ] **Step 5: Add license/source notes**

Create `apps/animus/NOTICE.md` with:
- ANIMA project license.
- Codex reference root path.
- Apache-2.0 source-adaptation rule.
- A table initially marked "No source files adapted yet".

- [ ] **Step 6: Check compile**

Run:

```bash
cargo check -p animus
```

Expected: pass.

- [ ] **Step 7: Complete ticket and commit**

Update validation and commit:

```bash
git add Cargo.toml apps/animus/Cargo.toml apps/animus/src/main.rs apps/animus/NOTICE.md tickets/animus-coding-tui/ACT-002-rust-crate-scaffold-license.md tickets/animus-coding-tui/ACT-000-parent.md
git commit -m "animus: scaffold rust tui package"
```

---

## Task 3: ANIMA WebSocket Client

**Ticket:** `tickets/animus-coding-tui/ACT-003-anima-ws-client.md`

**Files:**
- Create: `apps/animus/src/config.rs`
- Create: `apps/animus/src/protocol.rs`
- Create: `apps/animus/src/client.rs`
- Modify: `apps/animus/src/main.rs`
- Test in same modules using `#[cfg(test)]`
- Reference old TS: `apps/animus/src/client/protocol.ts`, `apps/animus/src/client/connection.ts`
- Reference Codex: `codex-rs/app-server-transport/src/transport/websocket.rs`

- [ ] Define protocol structs/enums with serde names matching `ws.py`.
- [ ] Add deserialization tests for every server frame, including unknown-frame handling.
- [ ] Add serialization tests for auth, tool schemas, user messages, tool results, approvals, and cancel.
- [ ] Implement config loading precedence: CLI args, environment variables, old `.animus` config if available, defaults.
- [ ] Implement connect/auth/tool-schema registration and reconnect with bounded backoff.
- [ ] Track `current_run_id` from `run_started`.
- [ ] Expose send helpers: `send_user_message`, `send_tool_result`, `send_approval_response`, `send_cancel_current_run`.
- [ ] Run:

```bash
cargo test -p animus protocol client config
```

- [ ] Complete ticket and commit:

```bash
git add apps/animus/src/config.rs apps/animus/src/protocol.rs apps/animus/src/client.rs apps/animus/src/main.rs tickets/animus-coding-tui/ACT-003-anima-ws-client.md tickets/animus-coding-tui/ACT-000-parent.md
git commit -m "animus: add anima websocket client"
```

---

## Task 4: Terminal Event Loop and Basic Transcript

**Ticket:** `tickets/animus-coding-tui/ACT-004-terminal-transcript.md`

**Files:**
- Create: `apps/animus/src/app.rs`
- Create: `apps/animus/src/tui.rs`
- Create: `apps/animus/src/transcript.rs`
- Modify: `apps/animus/src/main.rs`
- Reference Codex: `codex-rs/tui/src/tui.rs`, `codex-rs/tui/src/tui/event_stream.rs`, `codex-rs/tui/src/history_cell/*.rs`

- [ ] Add `AppState`, `RunState`, `ConnectionState`, `TranscriptItem`, and `AppEvent`.
- [ ] Write reducer tests for `run_started`, stream token append, reasoning, tool call, tool return, error, cancelled, and turn complete.
- [ ] Add terminal setup/teardown with raw mode, alternate screen, panic-safe cleanup, and Ctrl-C/Ctrl-D exit.
- [ ] Render a transcript list plus input/status placeholder.
- [ ] Convert server frames into app events before mutating state.
- [ ] Add live assistant streaming into an active assistant transcript item.
- [ ] Run:

```bash
cargo test -p animus app transcript tui
cargo run -p animus -- --headless
```

- [ ] Complete ticket and commit:

```bash
git add apps/animus/src/app.rs apps/animus/src/tui.rs apps/animus/src/transcript.rs apps/animus/src/main.rs tickets/animus-coding-tui/ACT-004-terminal-transcript.md tickets/animus-coding-tui/ACT-000-parent.md
git commit -m "animus: render rust terminal transcript"
```

---

## Task 5: Local Tools and Permissions

**Ticket:** `tickets/animus-coding-tui/ACT-005-local-tools-permissions.md`

**Files:**
- Create: `apps/animus/src/tools/mod.rs`
- Create: `apps/animus/src/tools/shell.rs`
- Create: `apps/animus/src/tools/files.rs`
- Create: `apps/animus/src/tools/process.rs`
- Create: `apps/animus/src/permissions.rs`
- Reference old TS: `apps/animus/src/tools/*.ts`
- Reference Codex: `codex-rs/tui/src/exec_command.rs`, `codex-rs/tui/src/exec_cell/*`, `codex-rs/shell-command`, `codex-rs/execpolicy`

- [ ] Port tool schema names from old TypeScript: `bash`, `read_file`, `write_file`, `edit_file`, `grep`, `glob`, `list_dir`, `multi_edit`, `ask_user`, `todo_write`, `todo_read`, `bg_start`, `bg_output`, `bg_stop`, `bg_list`.
- [ ] Write permission tests first for read-only, workspace-write, shell ask, shell allow, and dangerous command deny/ask.
- [ ] Implement path normalization and workspace containment checks.
- [ ] Implement file read/write/edit/list/search with structured success/error outputs.
- [ ] Implement shell execution with timeout, stdout/stderr capture, and cancellation hook.
- [ ] Implement background process registry for `bg_*` tools.
- [ ] Wire tool execution to `tool_execute` frames and send `tool_result` frames.
- [ ] Run:

```bash
cargo test -p animus tools permissions
```

- [ ] Complete ticket and commit:

```bash
git add apps/animus/src/tools apps/animus/src/permissions.rs tickets/animus-coding-tui/ACT-005-local-tools-permissions.md tickets/animus-coding-tui/ACT-000-parent.md
git commit -m "animus: add local action tools"
```

---

## Task 6: Input, Slash Commands, and Status Line

**Ticket:** `tickets/animus-coding-tui/ACT-006-input-commands-status.md`

**Files:**
- Create: `apps/animus/src/input.rs`
- Create: `apps/animus/src/commands.rs`
- Modify: `apps/animus/src/tui.rs`
- Modify: `apps/animus/src/app.rs`
- Reference Codex: `codex-rs/tui/src/slash_command.rs`, `codex-rs/tui/src/bottom_pane/textarea.rs`, `codex-rs/tui/src/bottom_pane/footer.rs`, `codex-rs/tui/src/status/*`

- [ ] Add input buffer tests for insertion, backspace/delete, cursor movement, multiline entry, history previous/next.
- [ ] Add command parser tests for `/help`, `/clear`, `/cancel`, `/reconnect`, `/permissions`, `/status`, `/diff`, `/spawns`, `/cancel-spawn <id>`, `/quit`.
- [ ] Implement command metadata: name, description, argument mode, sort order, busy-state availability.
- [ ] Implement autocomplete/filtering for slash commands.
- [ ] Render status line items: connection, cwd, permission mode, approval mode, current run, spawn count, queued/background process count.
- [ ] Wire `/cancel` to `send_cancel_current_run`.
- [ ] Run:

```bash
cargo test -p animus input commands app
```

- [ ] Complete ticket and commit:

```bash
git add apps/animus/src/input.rs apps/animus/src/commands.rs apps/animus/src/tui.rs apps/animus/src/app.rs tickets/animus-coding-tui/ACT-006-input-commands-status.md tickets/animus-coding-tui/ACT-000-parent.md
git commit -m "animus: add input commands and status"
```

---

## Task 7: Inline Approvals

**Ticket:** `tickets/animus-coding-tui/ACT-007-inline-approvals.md`

**Files:**
- Create: `apps/animus/src/approvals.rs`
- Modify: `apps/animus/src/app.rs`
- Modify: `apps/animus/src/tui.rs`
- Modify: `apps/animus/src/permissions.rs`
- Modify if needed: `apps/server/src/anima_server/api/routes/ws.py`
- Reference Codex: `codex-rs/tui/src/approval_events.rs`, `codex-rs/tui/src/bottom_pane/approval_overlay.rs`, `codex-rs/tui/src/history_cell/approvals.rs`

- [ ] Add approval state tests for pending, accept, accept-for-session, deny, cancel, disappeared-after-reconnect.
- [ ] Model shell approvals separately from file-change approvals.
- [ ] Render approval transcript item plus bottom approval controls.
- [ ] Support keyboard decisions and explicit slash-command fallback.
- [ ] Send `approval_response` with `run_id`, `tool_call_id`, approved boolean, and optional reason.
- [ ] Remember session decisions where permission policy allows it.
- [ ] Run:

```bash
cargo test -p animus approvals app permissions
bun run test:server -- apps/server/tests/test_ws.py -q
```

- [ ] Complete ticket and commit:

```bash
git add apps/animus/src/approvals.rs apps/animus/src/app.rs apps/animus/src/tui.rs apps/animus/src/permissions.rs apps/server/src/anima_server/api/routes/ws.py tickets/animus-coding-tui/ACT-007-inline-approvals.md tickets/animus-coding-tui/ACT-000-parent.md
git commit -m "animus: add inline approval flow"
```

---

## Task 8: ANIMA Spawn/Thread Visibility

**Ticket:** `tickets/animus-coding-tui/ACT-008-spawn-thread-visibility.md`

**Files:**
- Create: `apps/animus/src/spawns.rs`
- Modify: `apps/animus/src/protocol.rs`
- Modify: `apps/animus/src/tui.rs`
- Modify: `apps/animus/src/commands.rs`
- Modify server frame emission if spawn events are missing
- Reference: `docs/prds/three-tier-architecture/P8-n-agent-spawning.md`
- Reference Codex: `codex-rs/tui/src/multi_agents.rs`, `codex-rs/tui/src/app/agent_navigation.rs`, `codex-rs/tui/src/app/loaded_threads.rs`

- [ ] Read `P8-n-agent-spawning.md` before changing server or UI semantics.
- [ ] Add typed spawn statuses: queued, running, completed, failed, cancelled.
- [ ] Add protocol tests for spawn event frames.
- [ ] Render running spawn count in the status line.
- [ ] Implement `/spawns` list view with id, task preview, status, started/completed timestamps when available.
- [ ] Implement `/cancel-spawn <id>` only if server support exists; otherwise show a clear unsupported state.
- [ ] Ensure labels say "background process" or "spawn", not separate assistant/persona.
- [ ] Run:

```bash
cargo test -p animus spawns commands protocol
```

- [ ] Complete ticket and commit:

```bash
git add apps/animus/src/spawns.rs apps/animus/src/protocol.rs apps/animus/src/tui.rs apps/animus/src/commands.rs tickets/animus-coding-tui/ACT-008-spawn-thread-visibility.md tickets/animus-coding-tui/ACT-000-parent.md
git commit -m "animus: show anima background spawns"
```

---

## Task 9: Replace Bun Wiring, Docs, and Smoke Tests

**Ticket:** `tickets/animus-coding-tui/ACT-009-replace-bun-smoke-docs.md`

**Files:**
- Remove/replace: `apps/animus/package.json`
- Remove/replace: `apps/animus/tsconfig.json`
- Remove/replace: `apps/animus/src/**/*.ts`
- Modify: `package.json`
- Modify: `docs/prds/animus/rust-coding-tui-v1.md`
- Modify: `docs/superpowers/specs/2026-06-27-animus-rust-coding-tui-design.md`
- Modify: `tickets/animus-coding-tui/*.md`

- [ ] Remove Bun/Ink package wiring once the Rust TUI covers the required flow.
- [ ] Update root scripts:
  - `dev:animus` should run the Rust binary or `cargo run -p animus`.
  - `test:animus` should run `cargo test -p animus`.
  - Build wiring should include Animus if it becomes part of `bun run build`.
- [ ] Update docs to reflect Rust-native Animus and remove Bun/Ink fallback language.
- [ ] Run full validation:

```bash
cargo test -p animus
cargo check -p animus
bun run build
bun run test
```

- [ ] Start the backend and verify health:

```bash
bun run dev:server
curl http://127.0.0.1:3031/health
```

Expected: HTTP 200 health response.

- [ ] Smoke-test Animus:
  - connect/auth to `/ws/agent`,
  - prompt -> stream -> delegated safe tool -> completion,
  - approval-required -> approve -> completion,
  - cancel active run,
  - reconnect after websocket close,
  - `/spawns` with mocked or real spawn event.
- [ ] Update every child ticket validation, parent completed-ticket history, PRD status, and changed paths.
- [ ] Commit:

```bash
git add apps/animus package.json docs/prds/animus/rust-coding-tui-v1.md docs/superpowers/specs/2026-06-27-animus-rust-coding-tui-design.md tickets/animus-coding-tui
git commit -m "animus: replace bun cli with rust tui"
```

---

## Final PR Checklist

- [ ] `git status --short` shows only intended changes.
- [ ] `cargo test -p animus` passes.
- [ ] `bun run build` passes.
- [ ] `bun run test` passes.
- [ ] `/health` returns HTTP 200 while the server is running.
- [ ] Manual Animus smoke tests are recorded in `ACT-009`.
- [ ] `apps/animus/NOTICE.md` lists any adapted Codex source files.
- [ ] Parent ticket lists completed child tickets with timestamps.
- [ ] PR description includes affected areas, validation, source-adaptation notes, and migration/setup notes.
