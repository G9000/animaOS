# GWR-002 - Add gateway-style request context contract

- Status: done
- Priority: P1
- Scope: `apps/server`
- Parent: `GWR-000`
- Depends on: `GWR-001`
- Owner: codex-agent
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-27 05:12 MYT
- Started: 2026-06-27 05:12 MYT
- Completed: 2026-06-27 05:12 MYT

## Goal

Define one request context object that gateway code passes to runtime code for every authenticated request.

## Deliverables

- Add typed request context with `user_id`, `device_id`, `session_id`, `trace_id`
- Thread context through runtime entry points
- Stop reading ambient auth state from unrelated layers

## Acceptance

- Chat/runtime services accept the new context type
- Context fields are available in logs and runtime orchestration
- Tests cover missing and valid context cases


## Activity Log
- 2026-06-27 05:12 MYT - Marked complete in this branch handoff for PR closure.

- 2026-06-26 16:38 MYT - Ticket created and normalized to the repo ticket workflow.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
