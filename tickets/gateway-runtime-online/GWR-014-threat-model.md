# GWR-014 - Create gateway/runtime threat model

- Status: backlog
- Priority: P1
- Scope: gateway + runtime
- Parent: `GWR-000`
- Depends on: `GWR-002`, `GWR-010`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-gateway-runtime-online-delivery.md
- Created: 2026-06-26 17:18 MYT
- Updated: 2026-06-26 17:18 MYT
- Started:
- Completed:

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
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
