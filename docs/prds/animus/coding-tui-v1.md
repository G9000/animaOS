---
title: Animus Coding TUI Short Port v1
author: Julio Caesar
last_edited: 2026-06-26
version: 1
status: draft
plan: ../../superpowers/plans/2026-06-26-animus-coding-tui.md
spec: ../../superpowers/specs/2026-06-26-animus-coding-tui-design.md
---

# Animus Coding TUI Short Port v1

| Field | Value |
| --- | --- |
| Author | Julio Caesar |
| Version | 1 |
| Status | Draft |
| Created | 2026-06-26 |
| Last edited | 2026-06-26 |
| Spec | [Animus Coding TUI Short Port Design](../../superpowers/specs/2026-06-26-animus-coding-tui-design.md) |
| Plan | [Implementation Plan](../../superpowers/plans/2026-06-26-animus-coding-tui.md) |

> Make Animus immediately useful as an ANIMA-first coding TUI by short-porting proven coding TUI patterns without replacing ANIMA's runtime.

## Context

Animus already connects to the ANIMA server over WebSocket and can execute local action tools. The current TUI is functional but thin: the transcript is simple, approvals are basic, slash commands are hardcoded, streaming state is limited, and cancel/approval lifecycle has protocol gaps.

A mature Bun/Ink coding-agent terminal codebase provides useful terminal UX patterns. Its Apache-2.0 source can be used as a reference and selectively adapted, excluding upstream brand assets.

ANIMA's backend is already close enough in shape to benefit from the same CLI-to-agent-server boundary. ANIMA should keep its own server, memory, identity model, and single-identity background spawning design.

## What This Version Delivers

- An Animus TUI structure with separate coordinator, view, command routing, transcript, approval, and backend adapter responsibilities.
- A richer terminal experience for coding: readable streamed transcript, useful status line, command autocomplete/history, inline approvals, and better tool-call rendering.
- Fixed run lifecycle support between Animus and `/ws/agent`, including `run_started`, `cancelled`, `run_id`, approval responses, and cancel.
- ANIMA-native spawn visibility and basic controls so background cognitive work is visible from the CLI.
- Clear source adaptation boundary and license hygiene for copied/adapted upstream UI pieces.

## What Users See

- Animus starts into a coding-focused terminal interface connected to ANIMA.
- User prompts stream smoothly and show reasoning/tool activity without noisy raw JSON.
- Slash commands are discoverable and responsive.
- Tool approvals appear inline and can be allowed, denied, or remembered.
- Background spawned tasks are visible as ANIMA work, not as separate people or chat participants.
- Cancel/reconnect/clear/help behave predictably.

## Rules and Constraints

- ANIMA server is required for v1.
- Animus remains a coding tool first.
- Reference backend, memory, cloud, and brand-specific systems are not ported wholesale.
- Copied/adapted upstream UI code must respect Apache-2.0 notices and must not include excluded brand assets.
- Spawned workers are single-identity background processes and cannot talk directly to the user.
- Spawned workers do not get dangerous delegated client tools by default.

## Success Metrics

| Metric | Target | How to measure |
| --- | --- | --- |
| Turn lifecycle correctness | Prompt, stream, delegated tool result, approval, cancel, and completion paths work | Focused tests plus local smoke test |
| TUI usability | Common coding session can run without raw protocol noise or manual restart | Manual smoke test using `apps/animus` |
| Protocol coverage | All server frames emitted by `/ws/agent` have Animus types and handlers | Typecheck and protocol tests |
| Approval reliability | Approve, deny, and always flows round-trip to server | Unit/integration tests |
| Spawn visibility | Running/completed/failed spawns render in Animus | Spawn event tests or mocked stream test |
| License hygiene | adapted files have source notes and no brand assets | File audit before merge |

## Out of Scope

- Standalone provider mode without ANIMA.
- External cloud/browser/desktop integrations.
- Full multi-agent UX where users chat with subagents directly.
- Recursive spawning.
- Direct dangerous action-tool delegation to spawned workers.
- Large ANIMA memory architecture changes.

## References

- [Design Spec](../../superpowers/specs/2026-06-26-animus-coding-tui-design.md)
- [Implementation Plan](../../superpowers/plans/2026-06-26-animus-coding-tui.md)
- [Existing Animus CLI Plan](../../superpowers/plans/2026-03-23-animus-cli.md)
- [N-Agent Spawning PRD](../three-tier-architecture/P8-n-agent-spawning.md)
