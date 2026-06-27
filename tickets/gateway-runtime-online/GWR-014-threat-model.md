# GWR-014 - Create gateway/runtime threat model

- Status: done
- Priority: P1
- Scope: gateway + runtime
- Parent: `GWR-000`
- Depends on: `GWR-002`, `GWR-010`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-06-27 12:08 MYT
- Started: 2026-06-27 12:08 MYT
- Completed: 2026-06-27 12:08 MYT

## Goal

Create a threat model for the gateway/runtime online boundary before final hardening work.

## Deliverables

- Trust boundary map for clients, gateway, runtime, adapters, and storage
- Asset list for unlock tokens, device secrets, session state, DEKs, provider secrets, memory payloads, logs, and traces
- Attacker capability list for local malware, malicious localhost process, stolen device, replayed webhook, compromised adapter, and exposed public endpoint
- Control matrix for auth, nonce/replay, rate limits, device revocation, logging, and secret handling
- Accepted-risk list with owner and revisit date

## Acceptance

- Threat model references the gateway/runtime request-context contract
- Each public or future-public ingress path has an auth/replay/logging decision
- Final security hardening ticket can execute from the threat model without rediscovering boundaries

## Activity Log

- 2026-06-26 17:18 MYT - Ticket created.

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