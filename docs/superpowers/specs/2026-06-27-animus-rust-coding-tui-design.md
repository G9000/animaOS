# Animus Rust Coding TUI Design

## Summary

Animus should be rewritten as a Rust-native ANIMA-first coding terminal. The current Bun/Ink implementation is early enough to replace outright instead of preserving as a fallback.

The rewrite should use proven Rust coding-agent TUI architecture patterns while keeping ANIMA's server, memory, identity, and runtime boundaries as the source of truth.

## Goals

- Replace the current Bun/Ink `apps/animus` implementation with a Rust CLI/TUI.
- Keep ANIMA server-driven: Animus connects to `/ws/agent`; ANIMA owns the agent loop, memory, model calls, and runtime state.
- Provide a serious coding terminal: responsive input, readable transcript, rich tool output, inline approvals, status line, slash commands, reliable cancel/reconnect, and spawn visibility.
- Use Rust for performance, single-binary distribution, better terminal control, and stronger protocol/state modeling.
- Preserve licensing boundaries: copy/adapt Apache-2.0-compatible code only, retain notices where source is adapted, and do not copy upstream product names, logos, ASCII art, or brand assets.

## Non-Goals

- Do not keep the Bun/Ink CLI as a supported fallback in v1.
- Do not port the reference backend, cloud API model, auth system, provider stack, memory model, or full multi-agent process manager wholesale.
- Do not add standalone non-ANIMA provider mode in this version.
- Do not redesign ANIMA's memory or identity architecture.
- Do not expose spawned workers as separate user-facing personalities. ANIMA remains one identity with multiple background processes.

## Current State

`apps/animus` already has a Bun/Ink TUI, WebSocket client, local action tools, permission checks, and headless mode. The current implementation proves the flow but is thin:

- `run_started` and `cancelled` are emitted server-side but are missing from the current Animus protocol types.
- Cancel messages need a current `run_id`.
- Approval response handling is incomplete server-side.
- The TUI has no rich transcript, queue, status line, robust approval rendering, or native terminal control.
- The current app structure would require substantial refactoring before it feels like a serious coding tool.

ANIMA already has a PRD for N-agent spawning in `docs/prds/three-tier-architecture/P8-n-agent-spawning.md`. This initiative should surface that capability in Animus, not invent a separate subagent identity model.

## Architecture

The target shape is:

```text
Rust Animus TUI
  -> ANIMA WebSocket adapter
  -> /ws/agent
  -> ANIMA agent runtime
  -> streamed events back to Animus
```

`apps/animus` should become a Rust package with focused modules:

- `main`: CLI args and app startup.
- `config`: local config, server URL, workspace path, token lookup.
- `protocol`: typed ANIMA WebSocket client/server frames.
- `client`: auth, connect/reconnect, send queue, run lifecycle, frame dispatch.
- `tui`: terminal initialization, event loop, layout, rendering.
- `app`: central UI state reducer for transcript, input, approvals, tools, status, and spawns.
- `transcript`: history item model and renderers.
- `input`: editor buffer, history, slash command autocomplete, key handling.
- `commands`: slash command registry and routing.
- `approvals`: approval models, decision routing, and UI rendering.
- `tools`: local action tool schemas, execution, and result formatting.
- `permissions`: local shell/file permission rules.
- `spawns`: ANIMA background worker status model and UI.

The server-side changes stay narrow: fix frame coverage, approval response handling, cancel/run-id behavior, and spawn lifecycle frames if missing.

## Source Adaptation Boundary

Copy/adapt where the code is generic and license-compatible:

- Terminal event-loop structure.
- Transcript/history cell model.
- Status line concepts.
- Approval request/decision modeling.
- Slash command metadata and filtering shape.
- Multi-agent/thread navigation presentation concepts.

Rewrite around ANIMA where the code is product-specific:

- Backend adapter and protocol names.
- Authentication/account logic.
- Memory, identity, and profile commands.
- Model/provider settings.
- Cloud/web/desktop links.
- Upstream brand text, names, logos, and ASCII art.

## Concrete Codex Reference Boundary

Use the local Codex checkout at `C:\Users\leoca\OneDrive\Desktop\anima\codex` as a reference implementation for Rust TUI structure, not as a product template. The implementation plan maps specific Codex files to Animus tasks; the key boundaries are:

- Terminal lifecycle/event-loop patterns may be adapted from `codex-rs/tui/src/tui.rs`, `codex-rs/tui/src/tui/event_stream.rs`, and `codex-rs/tui/src/tui/frame_requester.rs`.
- Transcript/history-cell patterns may be adapted from `codex-rs/tui/src/history_cell/*.rs`, but Animus cells must be ANIMA event cells.
- Slash-command and status-line patterns may be adapted from `codex-rs/tui/src/slash_command.rs`, `codex-rs/tui/src/bottom_pane/slash_commands.rs`, `codex-rs/tui/src/bottom_pane/footer.rs`, and `codex-rs/tui/src/status/*`.
- Approval display and decision modeling may be adapted from `codex-rs/tui/src/approval_events.rs`, `codex-rs/tui/src/bottom_pane/approval_overlay.rs`, and `codex-rs/tui/src/history_cell/approvals.rs`.
- Background thread navigation concepts may be adapted from `codex-rs/tui/src/multi_agents.rs` and `codex-rs/tui/src/app/agent_navigation.rs`, but labels and behavior must remain ANIMA single-identity spawn semantics.

Do not copy provider login, ChatGPT/OpenAI account state, cloud task APIs, remote thread stores, app-server session assumptions, product names, logos, ASCII art, marketing copy, or model-provider defaults. Any adapted source file must be listed in `apps/animus/NOTICE.md` before merge.

## Reference Architecture Alignment

The Rust rewrite should deliberately follow the local Apache-2.0 Rust coding-agent reference architecture in these areas:

- **Protocol-first turn control.** Model turn start, turn steering, and turn interrupt as explicit typed messages. Animus should track `thread_id`, `run_id` or turn id, client message id, current cwd, permissions, and expected active turn before sending cancel/steer-like requests.
- **Typed thread items.** Convert raw backend frames into a stable internal item model before rendering. The renderer should not switch directly on loose JSON.
- **App event bus.** Widgets should emit app-level events instead of mutating global state directly. The app loop owns shutdown, command execution, approval submission, reconnect, and thread/spawn switching.
- **History cells.** Transcript rendering should use small cell renderers for user messages, assistant messages, reasoning, shell execution, patch/file edits, plans/todos, approvals, notices, search, and session events.
- **Approval decisions.** Approval state should distinguish accept, accept-for-session, accept-with-policy-amendment when available, decline, and cancel. File-change approval and shell approval should have separate display models.
- **Slash command registry.** Commands should be enum/registry-driven with presentation order, descriptions, inline-arg support, and availability rules.
- **Status surfaces.** Status line items should be composable and configurable over time: run state, cwd/project, permission mode, approval mode, context/usage if available, thread/run id, spawn count, and task progress.
- **Multi-agent/thread presentation.** Background workers should have a picker/list model, status dot, task preview, running/closed state, and fast navigation concepts. Animus should map this onto ANIMA single-identity spawn semantics.

The implementation should translate these patterns into ANIMA vocabulary rather than copying backend/provider/auth/product assumptions.

## Subagents

This rewrite includes ANIMA-native spawn visibility because it is central to coding-tool usefulness.

The ANIMA model is single-identity spawning:

- Spawned workers are background cognitive processes, not separate identities.
- They work on bounded tasks and report results back to the main ANIMA process.
- They should not talk directly to the user.
- They should not recursively spawn other workers in this version.
- They should not get dangerous delegated client tools by default.

Animus should display:

- Running background task count.
- Per-spawn status: queued, running, completed, failed, cancelled.
- Completed summaries.
- Failure output when useful.

Animus should support slash commands:

- `/spawns` lists current/recent background work.
- `/cancel-spawn <id>` cancels one spawn when the server supports it.

## Data Flow

1. Animus starts, loads config, and connects to `/ws/agent`.
2. Animus authenticates and registers local action tool schemas.
3. User submits a prompt through the Rust TUI.
4. Animus appends an optimistic user transcript item and sends `user_message`.
5. Server emits `run_started` with `run_id`.
6. Server streams reasoning, assistant text, tool calls, tool returns, approvals, spawn events, and turn completion.
7. Animus maps raw frames into typed app events and transcript/spawn/status state.
8. Delegated tool calls execute locally after permission checks and return `tool_result`.
9. Approval responses go back through `approval_response`.
10. Cancel sends `cancel` with current `run_id`.

## Error Handling

- Connection loss should show reconnectable state without losing the visible transcript.
- Unknown frames should become non-fatal diagnostic events.
- Tool execution failures should render as tool errors and still return structured results to the server.
- Approval recovery should restore pending approvals after reconnect when server state supports it.
- Cancel should be idempotent from the UI perspective.
- Terminal cleanup must restore raw mode, alternate screen, and cursor state on panic or normal exit.

## Testing

Focused tests should cover:

- Rust protocol serialization/deserialization for all server frames.
- App reducer behavior for stream, tool, approval, cancel, reconnect, and spawn events.
- Slash command parsing and busy-state behavior.
- Transcript rendering snapshots for assistant/tool/error/spawn cells.
- Permission decisions for shell/file tools.
- Server WebSocket tests for approval response and cancel.
- Smoke test: start server, run Animus, send a prompt, render streamed output, execute a delegated safe tool, and complete a turn.

## Rollout

The rewrite should land in slices:

1. Rust package scaffold and license/source boundary.
2. ANIMA WebSocket protocol/client.
3. Terminal event loop and basic transcript.
4. Local tools and permissions.
5. Slash commands, input history, and status line.
6. Inline approvals.
7. Spawn/thread visibility.
8. Replace Bun package wiring, docs, and smoke tests.
