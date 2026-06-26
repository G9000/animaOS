# ACT-007 - Add ANIMA-native spawn visibility and commands

- Status: backlog
- Priority: P1
- Scope: `apps/animus`, `apps/server`
- Parent: `ACT-000`
- Depends on: `ACT-001`, `ACT-003`
- Owner: unassigned
- PRD: docs/prds/animus/coding-tui-v1.md
- Plan: docs/superpowers/plans/2026-06-26-animus-coding-tui.md
- Created: 2026-06-26 18:51 MYT
- Updated: 2026-06-26 18:51 MYT
- Started:
- Completed:

## Goal

Expose ANIMA's single-identity background spawning model in Animus so coding work can use background cognitive workers without presenting them as separate chat agents.

## Deliverables

- Spawn event protocol coverage.
- Spawn status store.
- Spawn status display in transcript or status area.
- `/spawns` command.
- `/cancel-spawn <id>` command when server support exists.

## Acceptance

- Running, completed, failed, and cancelled spawn states can be represented.
- Users can inspect background work from Animus.
- Spawned workers are labeled as ANIMA background processes, not independent personas.
- Dangerous delegated action tools are not exposed to spawns by default.

## Activity Log

- 2026-06-26 18:51 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only

