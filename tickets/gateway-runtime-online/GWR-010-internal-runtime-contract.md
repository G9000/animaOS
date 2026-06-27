# GWR-010 - Add internal gateway-to-runtime contract

- Status: done
- Priority: P2
- Scope: gateway + runtime
- Parent: `GWR-000`
- Depends on: `GWR-002`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-27 12:08 MYT
- Started: 2026-06-27 12:08 MYT
- Completed: 2026-06-27 12:08 MYT

## Goal

Define the internal contract the gateway uses to call runtime services once those layers are separated.

## Deliverables

- Typed request/response contract for chat and related runtime actions
- Error translation policy
- Timeout and retry rules

## Acceptance

- Gateway-runtime calls use a stable contract instead of route internals
- Runtime errors map cleanly to gateway responses
- Contract is documented and testable


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