# Animus Coding TUI Short Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `apps/animus` a highly usable ANIMA-first coding TUI by adapting proven coding TUI patterns and fixing the ANIMA WebSocket/run lifecycle needed to support them.

**Architecture:** Keep ANIMA server-driven. Animus owns terminal UX, local delegated action tools, permissions, and rendering; `/ws/agent` owns agent turns, memory, model calls, run state, approvals, and ANIMA-native spawn lifecycle. Use a thin backend adapter and normalized transcript model so adapted UI pieces do not depend on reference backend semantics.

**Tech Stack:** Bun, TypeScript, React, Ink, `ws`, Python, FastAPI, ANIMA agent runtime.

**Spec:** `docs/superpowers/specs/2026-06-26-animus-coding-tui-design.md`

**PRD:** `docs/prds/animus/coding-tui-v1.md`

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `apps/animus/src/client/protocol.ts` | Complete Animus protocol types for server frames and client messages |
| `apps/animus/src/client/connection.ts` | WebSocket connection, auth, reconnect, run lifecycle send helpers |
| `apps/animus/src/ui/App.tsx` | Temporary root entry; should become thin wrapper around coordinator |
| `apps/animus/src/ui/app/AppCoordinator.tsx` | New state/effects coordinator for streaming, commands, approvals, and connection |
| `apps/animus/src/ui/app/AppView.tsx` | New render-only Ink tree |
| `apps/animus/src/ui/app/useConversationLoop.ts` | New stream/run lifecycle state hook |
| `apps/animus/src/ui/app/useSubmitHandler.ts` | New user submit and slash command routing hook |
| `apps/animus/src/ui/app/useApprovalFlow.ts` | New approval state and approve/deny/always handlers |
| `apps/animus/src/ui/commands/registry.ts` | Built-in command definitions and metadata |
| `apps/animus/src/ui/commands/routing.ts` | Busy-state command routing |
| `apps/animus/src/ui/transcript/types.ts` | Normalized transcript item types |
| `apps/animus/src/ui/transcript/normalize.ts` | Raw server-frame to transcript/live-state normalization |
| `apps/animus/src/ui/transcript/StaticTranscript.tsx` | Committed transcript rendering |
| `apps/animus/src/ui/input/RichInput.tsx` | Rich input with history, hints, autocomplete, busy-state display |
| `apps/animus/src/ui/input/SlashCommandAutocomplete.tsx` | Slash command autocomplete |
| `apps/animus/src/ui/tool-rendering/ToolCallMessage.tsx` | Rich tool-call rendering dispatcher |
| `apps/animus/src/ui/approvals/ApprovalSwitch.tsx` | Approval renderer dispatcher |
| `apps/animus/src/ui/spawns/SpawnStatus.tsx` | ANIMA spawn status display |
| `apps/server/src/anima_server/api/routes/ws.py` | WebSocket protocol frame handling, approval/cancel fixes |
| `apps/server/src/anima_server/services/agent/service.py` | Run lifecycle/approval/cancel integration if needed |
| `apps/server/tests/test_ws.py` | WebSocket protocol tests |
| `apps/server/tests/test_delegation.py` | Delegated action tool regression tests |
| `apps/animus/src/**/*.test.ts` | Animus unit tests for command routing, normalization, approvals |
| `apps/animus/NOTICE.md` | License/source notes for source-adapted UI snippets |

---

## Task 1: Protocol and Run Lifecycle

**Files:**
- Modify: `apps/animus/src/client/protocol.ts`
- Modify: `apps/animus/src/client/connection.ts`
- Modify: `apps/server/src/anima_server/api/routes/ws.py`
- Test: `apps/server/tests/test_ws.py`
- Test: `apps/animus/src/client/protocol.test.ts`

- [ ] Add Animus protocol types for `run_started`, `cancelled`, approval frames, spawn status frames, and structured errors.
- [ ] Add current `run_id` tracking in the Animus connection/client state.
- [ ] Update cancel send path to include `run_id`.
- [ ] Implement or complete server handling for `approval_response`.
- [ ] Make cancel idempotent and return a terminal user-visible frame.
- [ ] Add focused server tests for approval response and cancel frame shape.
- [ ] Add TypeScript tests for protocol guards/normalization if guards exist.
- [ ] Run `bun run test` or focused Animus tests.
- [ ] Run `bun run test` for Python server if touched.

## Task 2: Source Adaptation Boundary and License Hygiene

**Files:**
- Create: `apps/animus/NOTICE.md`
- Modify/Create: copied/adapted files under `apps/animus/src/ui/**`

- [ ] Audit candidate upstream UI files and list which pieces are copied, adapted, or used only as reference.
- [ ] Create `apps/animus/NOTICE.md` noting Apache-2.0 source adaptation and excluded brand assets.
- [ ] Remove/avoid upstream product names, logos, ASCII art, docs links, cloud/ADE references, and product-specific copy.
- [ ] Add short source comments only to adapted files where required for clarity/license traceability.
- [ ] Verify no upstream brand assets were copied.

## Task 3: App Coordinator and Render-Only View Split

**Files:**
- Modify: `apps/animus/src/ui/App.tsx`
- Create: `apps/animus/src/ui/app/AppCoordinator.tsx`
- Create: `apps/animus/src/ui/app/AppView.tsx`
- Create: `apps/animus/src/ui/app/useConversationLoop.ts`
- Create: `apps/animus/src/ui/app/useSubmitHandler.ts`

- [ ] Extract connection and stream state from `App.tsx` into `AppCoordinator`.
- [ ] Extract render tree into `AppView`.
- [ ] Move user submit handling into `useSubmitHandler`.
- [ ] Move run lifecycle and streaming state into `useConversationLoop`.
- [ ] Preserve current behavior before adding richer UI.
- [ ] Add focused tests for submit routing if feasible.
- [ ] Run `bun run test` and `bun run build` for `apps/animus`.

## Task 4: Transcript and Tool Rendering

**Files:**
- Create: `apps/animus/src/ui/transcript/types.ts`
- Create: `apps/animus/src/ui/transcript/normalize.ts`
- Create: `apps/animus/src/ui/transcript/StaticTranscript.tsx`
- Modify: `apps/animus/src/ui/Chat.tsx`
- Modify: `apps/animus/src/ui/ToolCall.tsx`
- Create: `apps/animus/src/ui/tool-rendering/ToolCallMessage.tsx`

- [ ] Define normalized transcript item types for user, assistant, reasoning, tool call, tool result, command, error, approval, and spawn events.
- [ ] Convert raw server frames into normalized transcript/live items.
- [ ] Render committed items with stable keys and streaming/live items separately.
- [ ] Add rich rendering for bash, read/write/edit, grep/glob/list, todo, and spawn tools.
- [ ] Clip long outputs with clear truncation markers.
- [ ] Add tests for transcript normalization.
- [ ] Run focused tests and `bun run build`.

## Task 5: Rich Input, Slash Commands, and Status Line

**Files:**
- Create: `apps/animus/src/ui/input/RichInput.tsx`
- Create: `apps/animus/src/ui/input/SlashCommandAutocomplete.tsx`
- Create: `apps/animus/src/ui/commands/registry.ts`
- Create: `apps/animus/src/ui/commands/routing.ts`
- Modify: `apps/animus/src/ui/Header.tsx`
- Modify: `apps/animus/src/ui/Input.tsx`

- [ ] Define built-in commands: `/help`, `/clear`, `/cancel`, `/reconnect`, `/plan`, `/spawns`, `/cancel-spawn`, `/quit`.
- [ ] Add command metadata for labels, descriptions, busy-state rules, and autocomplete.
- [ ] Add input history and command autocomplete.
- [ ] Render connection/model/cwd/mode/current-run/spawn-count status.
- [ ] Keep the UI compact and coding-tool focused.
- [ ] Add command routing tests.
- [ ] Run focused tests and `bun run build`.

## Task 6: Inline Approval Flow

**Files:**
- Create: `apps/animus/src/ui/app/useApprovalFlow.ts`
- Create: `apps/animus/src/ui/approvals/ApprovalSwitch.tsx`
- Modify: `apps/animus/src/ui/Approval.tsx`
- Modify: `apps/animus/src/tools/permissions.ts`

- [ ] Model pending approval state separately from generic transcript state.
- [ ] Route approval rendering by tool type: shell, file edit/write, question, generic.
- [ ] Support allow, deny, and always where current permission model allows it.
- [ ] Send `approval_response` frames to server with the expected IDs.
- [ ] Recover gracefully if an approval disappears after reconnect.
- [ ] Add tests for approve/deny/always state changes.
- [ ] Run focused tests and smoke test an approval-producing command.

## Task 7: ANIMA-Native Spawn Visibility

**Files:**
- Create: `apps/animus/src/ui/spawns/types.ts`
- Create: `apps/animus/src/ui/spawns/store.ts`
- Create: `apps/animus/src/ui/spawns/SpawnStatus.tsx`
- Modify: `apps/animus/src/client/protocol.ts`
- Modify: `apps/server/src/anima_server/api/routes/ws.py`
- Reference: `docs/prds/three-tier-architecture/P8-n-agent-spawning.md`

- [ ] Confirm server frame names for spawn lifecycle, or define minimal frames if not implemented yet.
- [ ] Add Animus spawn event types for queued/running/completed/failed/cancelled.
- [ ] Render running spawn count in status line.
- [ ] Add `/spawns` command to list active/recent spawn work.
- [ ] Add `/cancel-spawn <id>` command when server support exists.
- [ ] Ensure spawned workers are presented as ANIMA background processes, not separate chat participants.
- [ ] Add mocked spawn event rendering tests.

## Task 8: Smoke Tests, Docs, and Tracker Closure

**Files:**
- Modify: `apps/animus/README.md` if present, otherwise add short usage notes in existing docs location.
- Modify: `tickets/animus-coding-tui/*.md`

- [ ] Run `bun run build`.
- [ ] Run `bun run test`.
- [ ] Smoke-test `/health`.
- [ ] Smoke-test Animus prompt -> stream -> delegated tool -> approval -> completion.
- [ ] Smoke-test cancel and reconnect.
- [ ] Smoke-test `/spawns` with mocked or real spawn events.
- [ ] Update tickets with validation and changed paths.
- [ ] Record any known follow-up tickets.
