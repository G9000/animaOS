# Animus Coding TUI Short Port Design

## Summary

Animus should become a highly usable ANIMA-first coding TUI by adapting proven terminal UX patterns while keeping ANIMA's server, memory, identity, and runtime boundaries as the source of truth.

This is a short-port initiative: it prioritizes immediate coding-tool usability over a full CLI rewrite or a standalone provider abstraction.

## Goals

- Make `apps/animus` feel like a serious coding assistant TUI: responsive input, readable transcript, rich tool output, inline approvals, useful status, and reliable cancel/reconnect behavior.
- Use proven coding TUI patterns as the primary source reference for terminal UX and app structure.
- Keep ANIMA server-driven: Animus connects to `/ws/agent`; ANIMA owns the agent loop, memory, model calls, and runtime state.
- Add minimal ANIMA-native subagent visibility and control so background spawn work is usable from the terminal.
- Preserve licensing boundaries: copy/adapt Apache-2.0-compatible code only, retain notices where source is adapted, and do not copy upstream product names, logos, ASCII art, or brand assets.

## Non-Goals

- Do not port the reference backend, cloud API model, memory model, channel stack, or full subagent process manager wholesale.
- Do not add standalone non-ANIMA provider mode in this version.
- Do not redesign ANIMA's memory or identity architecture.
- Do not expose spawned workers as separate user-facing personalities. ANIMA remains one identity with multiple background processes.

## Current State

`apps/animus` already has a Bun/Ink TUI, WebSocket client, local action tools, permission checks, and headless mode. The current root `App.tsx` owns too many responsibilities: connection handling, slash commands, transcript state, streaming state, approvals, tool execution, and rendering.

The server already supports delegated client-side tools through `/ws/agent`, but there are usability gaps:

- `run_started` and `cancelled` are emitted server-side but are missing from the Animus protocol types.
- Cancel messages need a current `run_id`.
- Approval response handling is incomplete server-side.
- The TUI has no rich transcript, queue, status line, or coding-tool-grade input/autocomplete.

ANIMA also already has a PRD for N-agent spawning in `docs/prds/three-tier-architecture/P8-n-agent-spawning.md`. This initiative should surface that capability in Animus, not invent a separate subagent identity model.

## Architecture

The target shape is:

```text
Animus TUI
  -> ANIMA backend adapter
  -> /ws/agent
  -> ANIMA agent runtime
  -> streamed events back to Animus
```

Animus should be split into focused modules:

- App coordinator: state, effects, stream lifecycle, command dispatch.
- Render-only app view: transcript, overlays, status, input.
- ANIMA backend adapter: WebSocket protocol, run lifecycle, reconnect, cancel, tool schema registration.
- Transcript model: normalized UI events independent from raw server frames.
- Command router: built-in slash commands, busy-state behavior, command metadata for autocomplete.
- Approval flow: inline approval state, approve/deny/always, recovery after reconnect.
- Tool rendering: bash/file/search/todo/spawn-specific displays.
- Spawn UI store: visible state for running/completed/failed background workers.

## Source Adaptation Boundary

Copy/adapt where the code is generic terminal UX:

- Input behavior and status-line patterns.
- Slash command autocomplete shape.
- Static transcript rendering pattern.
- Tool-call display ideas, including collapsed output and specialized renderers.
- Inline approval routing pattern.
- App split into coordinator, view, submit handler, approval flow, and conversation loop.

Rewrite around ANIMA where the code is product-specific:

- Backend adapter and protocol types.
- Memory, identity, and profile commands.
- Subagent runtime and process launching.
- Provider/model settings.
- Cloud/web/desktop/ADE links.
- Upstream brand text, names, logos, and ASCII art.

## Subagents

This initiative includes minimal subagent support because it is central to ANIMA's coding-tool usefulness.

The ANIMA model is single-identity spawning:

- Spawned workers are background cognitive processes, not separate identities.
- They can work on bounded tasks and report results back to the main ANIMA process.
- They should not talk directly to the user.
- They should not recursively spawn other workers in this version.
- They should not get dangerous delegated client tools by default.

Animus should display:

- Running background task count.
- Per-spawn status: queued, running, completed, failed, cancelled.
- Completed summaries.
- Failure output when useful.

Animus should support slash commands for user control:

- `/spawns` lists current/recent background work.
- `/cancel-spawn <id>` cancels one spawn when the server supports it.

The server-side spawn tools remain ANIMA-native:

- `spawn_task`
- `check_spawns`
- `cancel_spawn`
- `report_result` for spawn-only completion

## Data Flow

1. Animus connects and registers client-side action tools.
2. User submits a prompt.
3. Animus appends an optimistic user transcript item.
4. Animus sends `user_message` to `/ws/agent`.
5. Server emits `run_started` with `run_id`.
6. Server streams reasoning, assistant text, tool calls, tool returns, approvals, spawn events, and turn completion.
7. Animus normalizes raw frames into transcript and live-state events.
8. Delegated tool calls execute locally after permission checks and return `tool_result`.
9. Approval responses go back through `approval_response`.
10. Cancel sends `cancel` with current `run_id`.

## Error Handling

- Connection loss should show a reconnectable state without losing the visible transcript.
- Unknown frames should become dim diagnostic transcript events in development and non-fatal warnings in production.
- Tool execution failures should render as tool errors and still return structured results to the server.
- Approval recovery should restore pending approvals after reconnect when server state supports it.
- Cancel should be idempotent from the UI perspective: repeated cancel attempts should not corrupt state.

## Testing

Focused tests should cover:

- Protocol type coverage for all emitted server frames.
- Command routing and busy-state behavior.
- Transcript normalization from streamed frames.
- Approval approve/deny/always flows.
- Tool result routing.
- Cancel with current `run_id`.
- Spawn event rendering and commands.
- Smoke test: start server, connect Animus, send a prompt, render streamed output, execute a delegated safe tool, and complete a turn.

## Rollout

The short port should land in slices:

1. Protocol/run lifecycle fixes.
2. Source adaptation boundary and source-attribution setup.
3. TUI app split and transcript model.
4. Rich input, slash commands, and status line.
5. Tool rendering and inline approvals.
6. ANIMA-native spawn visibility and commands.
7. Smoke testing, docs, and polish.
