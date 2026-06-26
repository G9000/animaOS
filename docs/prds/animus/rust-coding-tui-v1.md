---
title: Animus Rust Coding TUI v1
author: Julio Caesar
last_edited: 2026-06-27
version: 1
status: draft
plan: ../../superpowers/plans/2026-06-27-animus-rust-coding-tui.md
spec: ../../superpowers/specs/2026-06-27-animus-rust-coding-tui-design.md
---

# Animus Rust Coding TUI v1

| Field | Value |
| --- | --- |
| Author | Julio Caesar |
| Version | 1 |
| Status | Draft |
| Created | 2026-06-27 |
| Last edited | 2026-06-27 |
| Spec | [Animus Rust Coding TUI Design](../../superpowers/specs/2026-06-27-animus-rust-coding-tui-design.md) |
| Plan | [Implementation Plan](../../superpowers/plans/2026-06-27-animus-rust-coding-tui.md) |

> Replace the current Bun/Ink Animus CLI with a Rust-native ANIMA-first coding terminal.

## Context

Animus is still early enough that a full rewrite is cheaper than carrying a TypeScript TUI forward. The current Bun/Ink implementation proves the server-driven flow, WebSocket action-tool delegation, and local permission model, but it is thin as a coding terminal and will become harder to make robust as approvals, transcript rendering, status surfaces, and background worker visibility grow.

The new direction is to replace `apps/animus` with a Rust-native terminal app using proven coding-agent TUI architecture patterns. ANIMA remains the backend: the Rust TUI connects to `/ws/agent`, executes local action tools, enforces local permissions, and renders ANIMA runtime events.

## What This Version Delivers

- A Rust workspace package for `apps/animus` that replaces the Bun/Ink CLI.
- A Rust WebSocket client for ANIMA `/ws/agent` auth, tool registration, streaming frames, tool results, approvals, cancel, and reconnect.
- A terminal UI with transcript/history cells, streaming assistant output, tool-call rendering, status line, command palette/slash commands, and inline approvals.
- Local action tools for shell and file/search operations, with a permission model suitable for coding work.
- ANIMA-native background spawn/thread visibility and control.
- License/source hygiene for any source-adapted upstream UI/protocol patterns.

## What Users See

- Running Animus starts a fast native terminal coding agent.
- ANIMA streams reasoning, assistant output, tool calls, approvals, and completion into a readable transcript.
- Slash commands expose common coding-session controls such as help, clear, cancel, reconnect, permissions, status, diff, spawns, and quit.
- Approvals show the requested shell/file/network-sensitive action and available decisions inline.
- Background spawned work is visible as ANIMA background cognition, not separate user-facing personalities.

## Rules and Constraints

- The Rust rewrite replaces the existing Bun/Ink CLI; no fallback app is kept as part of v1.
- ANIMA server is required for v1.
- Animus remains a coding tool first.
- Reference backend, cloud, auth, model-provider, and brand-specific systems are not ported wholesale.
- Copied/adapted upstream source must respect Apache-2.0 notices and must not include excluded brand assets.
- Spawned workers are single-identity background processes and cannot talk directly to the user.
- Spawned workers do not get dangerous delegated client tools by default.

## Success Metrics

| Metric | Target | How to measure |
| --- | --- | --- |
| Native replacement | `apps/animus` builds and runs as Rust, not Bun/Ink | Build scripts and package files reflect Rust CLI |
| Turn lifecycle correctness | Prompt, stream, delegated tool result, approval, cancel, and completion paths work | Focused Rust/server tests plus local smoke test |
| TUI usability | Common coding session can run without raw protocol noise or manual restart | Manual smoke test using `apps/animus` |
| Protocol coverage | All server frames emitted by `/ws/agent` have Rust types and handlers | Rust unit tests |
| Approval reliability | Approve, deny, and session/persistent decisions round-trip correctly | Unit/integration tests |
| Spawn visibility | Running/completed/failed spawns render in Animus | Spawn event tests or mocked stream test |
| License hygiene | Adapted files have source notes and no brand assets | File audit before merge |

## Out of Scope

- Standalone provider mode without ANIMA.
- External cloud/browser/desktop integrations.
- Keeping the Bun/Ink CLI as a supported fallback.
- Full multi-agent UX where users chat with subagents directly.
- Recursive spawning.
- Direct dangerous action-tool delegation to spawned workers.
- Large ANIMA memory architecture changes.

## References

- [Design Spec](../../superpowers/specs/2026-06-27-animus-rust-coding-tui-design.md)
- [Implementation Plan](../../superpowers/plans/2026-06-27-animus-rust-coding-tui.md)
- [Existing Animus CLI Plan](../../superpowers/plans/2026-03-23-animus-cli.md)
- [N-Agent Spawning PRD](../three-tier-architecture/P8-n-agent-spawning.md)

