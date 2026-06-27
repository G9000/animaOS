# GWR-012 - Add web and websocket delivery baseline

- Status: done
- Priority: P3
- Scope: `apps/site` + gateway
- Parent: `GWR-000`
- Depends on: `GWR-009`, `GWR-010`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-27 12:08 MYT
- Started: 2026-06-27 12:08 MYT
- Completed: 2026-06-27 12:08 MYT

## Goal

Provide a minimal web entry path that uses the same gateway policy and runtime contract as desktop.

## Deliverables

- Web login/session bootstrap
- Web chat request path
- Streaming response baseline

## Acceptance

- Web requests do not bypass gateway controls
- Streaming contract works for browser clients
- Runtime behavior stays consistent with desktop


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