# SUM-000 - Single-User Temporal Memory v2 Parent Tracker

- Status: in_progress
- Priority: P1
- Scope: `apps/server`, `docs/prds/memory`, `docs/architecture/memory`, `tickets/single-user-temporal-memory-v2`
- Depends on: none
- Owner: Codex
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-07-01 21:18 MYT
- Started: 2026-07-01 21:18 MYT
- Completed:

## Goal

Track the single-user temporal memory v2 initiative from baseline audit through evidence, temporal graph, profile, retrieval routing, salience, pattern synthesis, foresight, procedural learning, and optional adapter seams.

## Child Ticket Order

| Ticket | Title | Status | Depends on |
| --- | --- | --- | --- |
| `SUM-001` | Baseline memory truth audit and eval probes | `backlog` | none |
| `SUM-002` | Evidence baseline and episode quality | `backlog` | `SUM-001` |
| `SUM-003` | Temporal knowledge graph v2 | `backlog` | `SUM-002` |
| `SUM-004` | Structured user profile | `backlog` | `SUM-002` |
| `SUM-005` | Retrieval router and query plans | `backlog` | `SUM-003`, `SUM-004` |
| `SUM-006` | Salience-aware decay and soft evolution | `done` | `SUM-003`, `SUM-004` |
| `SUM-007` | Cross-episode pattern synthesis | `backlog` | `SUM-005`, `SUM-006` |
| `SUM-008` | Foresight signals | `backlog` | `SUM-002` |
| `SUM-009` | Procedural experience and skill memory | `backlog` | `SUM-005` |
| `SUM-010` | Optional external adapter seams | `backlog` | `SUM-003`, `SUM-005` |

## Deliverables

- A truth baseline for the live memory system.
- Evidence-backed durable memory semantics.
- Temporal knowledge graph relation lifecycle.
- Structured evidence-backed user profile.
- Intent-specific retrieval query plans.
- Salience-aware decay and evolution handling.
- Cross-episode pattern synthesis.
- Foresight signal extraction and lifecycle.
- Procedural experience extraction, clustering, and skill distillation.
- Optional external adapter seams that preserve SQLCipher as canonical storage.

## Acceptance

- Every child ticket references this parent.
- Parent status table reflects child progress.
- Each child ticket records validation and changed paths.
- Multi-user/group memory remains out of scope unless explicitly reauthorized.
- No external memory engine becomes mandatory.

## Completed Tickets

- `SUM-006` - Salience-aware decay and soft evolution (2026-07-01 21:18 MYT)

## Activity Log

- 2026-06-27 12:40 MYT - Parent tracker created for single-user temporal memory v2 planning.
- 2026-07-01 21:18 MYT - Marked SUM-006 done after implementing salience metadata, decay-class heat scoring, soft evolution chains, and sleep-time drift surfacing.

## Validation

- Commands:
  - not run (verification not requested in this session)
- Changed paths:
  - tickets/single-user-temporal-memory-v2/SUM-000-parent.md
- Notes:
  - Child ticket state updated for SUM-006.
