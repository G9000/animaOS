# GWR-002 - Add gateway-style request context contract

- Status: done
- Priority: P1
- Scope: `apps/server`
- Parent: `GWR-000`
- Depends on: `GWR-001`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-27 12:08 MYT
- Started: 2026-06-27 12:08 MYT
- Completed: 2026-06-27 12:08 MYT

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

- 2026-06-26 16:38 MYT - Ticket created and normalized to the repo ticket workflow.

## Validation

- Commands:
  - `rg -n "<<<<<<<|=======|>>>>>>>" apps/server apps/desktop apps/local-runtime-daemon`
  - `git status --short`
  - `git diff --check`
- Changed paths:
  - apps/server/src/anima_server/api/deps/__init__.py
  - apps/server/src/anima_server/api/deps/unlock.py
  - apps/server/src/anima_server/api/routes/auth.py
  - apps/server/src/anima_server/api/routes/chat.py
  - apps/server/src/anima_server/api/routes/devices.py
  - apps/server/src/anima_server/api/routes/webhook.py
  - apps/server/src/anima_server/auth/context.py
  - apps/server/src/anima_server/auth/extractor.py
  - apps/server/src/anima_server/auth/middleware.py
  - apps/server/src/anima_server/auth/policy.py
  - apps/server/src/anima_server/contracts/runtime.py
  - apps/server/src/anima_server/main.py
  - apps/server/src/anima_server/services/gateway_runtime.py
  - apps/server/src/anima_server/services/sessions.py
  - apps/desktop/src/lib/api.ts
  - apps/desktop/src/pages/chat/Chat.tsx
  - apps/desktop/src/components/AuthImage.tsx
  - apps/desktop/src/hooks/useAgentProfile.ts
  - apps/local-runtime-daemon/src/main.rs
- Notes:
  - Completed in one pass with shared auth/session/request-context, middleware, and compatibility surfaces.