# Gateway Runtime Online

This folder tracks the local ticket backlog for making the current local-first runtime safely reachable through a gateway layer without rewriting cognition internals.

Agents claiming work in this folder should follow [prd-ticket-workflow.md](../../docs/ops/prd-ticket-workflow.md).

Parent tracker: [GWR-000-parent.md](./GWR-000-parent.md)

## Order

1. `GWR-001` Extract auth primitives
2. `GWR-002` Add request context contract
3. `GWR-010` Add internal gateway-runtime contract
4. `GWR-003` Add gateway middleware
5. `GWR-008` Add trace and audit flow
6. `GWR-009` Add desktop compatibility bridge
7. `GWR-004` Add device lifecycle APIs
8. `GWR-005` Add trust policy and nonce store
9. `GWR-006` Standardize webhook ingress
10. `GWR-007` Add outbound adapter abstraction
11. `GWR-011` Document multi-device onboarding
12. `GWR-012` Add web delivery baseline
13. `GWR-014` Create gateway/runtime threat model
14. `GWR-013` Run security hardening pass

## Done Condition

- Local single-user mode still works unchanged.
- Gateway policy is centralized at ingress.
- Runtime core receives typed context instead of reading auth state ad hoc.
- New clients and third-party channels can use the same normalized runtime entry path.
