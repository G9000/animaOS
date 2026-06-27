# GWR-013 - Security hardening pass and acceptance

- Status: done
- Priority: P1
- Scope: all layers
- Parent: `GWR-000`
- Depends on: `GWR-005`, `GWR-008`, `GWR-010`, `GWR-014`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 16:38 MYT
- Updated: 2026-06-27 12:08 MYT
- Started: 2026-06-27 12:08 MYT
- Completed: 2026-06-27 12:08 MYT

## Goal

Finish the boundary work with one explicit hardening pass before broader online exposure.

## Deliverables

- Header/method/CSRF review where applicable
- Secret rotation and incident rollback notes
- Abuse limits and audit retention review

## Acceptance

- Threat-sensitive edges are reviewed and documented
- Rotation and revoke paths are tested
- Online deployment can be enabled without known critical auth gaps


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