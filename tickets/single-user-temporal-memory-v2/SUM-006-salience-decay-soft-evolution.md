# SUM-006 - Salience-aware decay and soft evolution

- Status: backlog
- Priority: P1
- Scope: `apps/server/src/anima_server/services/agent`, `apps/server/src/anima_server/models`
- Parent: `SUM-000`
- Depends on: `SUM-003`, `SUM-004`
- Owner: unassigned
- PRD: docs/prds/memory/single-user-temporal-memory-v2.md
- Plan: docs/superpowers/plans/2026-06-27-single-user-temporal-memory-v2.md
- Created: 2026-06-27 12:40 MYT
- Updated: 2026-06-27 12:40 MYT
- Started:
- Completed:

## Goal

Make memory decay and contradiction handling sensitive to emotional salience, identity stability, and gradual change.

## Deliverables

- Salience fields or sidecar model for memory class, emotional salience, stability, relationship proximity, and evidence strength.
- Extraction schema updates for salience.
- Heat scoring updates using decay classes.
- Soft evolution detector for preference and emotional changes.
- Sleep-time surfacing of possible memory drift.

## Acceptance

- Important identity and life-event memories decay more slowly than casual observations.
- Repeated low-grade emotional patterns can accumulate salience.
- Soft changes create evolution chains instead of deleting useful history.
- Tests cover decay math, salience parsing, and evolution handling.

## Activity Log

- 2026-06-27 12:40 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - none
