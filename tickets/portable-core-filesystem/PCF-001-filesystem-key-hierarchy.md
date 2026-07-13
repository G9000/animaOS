# PCF-001 - Filesystem key hierarchy and credential generations

- Status: backlog
- Priority: P0
- Scope: `packages/anima-corefs`, `packages/anima-core`, `apps/server`, `apps/desktop`, and `packages/api-client` crypto, manifest, Soul keyslots, credential UI/API
- Parent: `PCF-000`
- Depends on: none
- Owner: unassigned
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-1-filesystem-key-hierarchy-and-credential-generations`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-07-12 17:34 MYT
- Started:
- Completed:

## Goal

Add password/recovery keyslots, Filesystem Root Key subkeys, per-object DEKs, and crash-safe credential generations without changing content authority yet.

## Deliverables

- Versioned manifest and `soul_keyslots` records.
- Stable opaque owner UUID provisioning and complete password/recovery `user_keys` domain backfill before AAD-bound slots activate.
- Password and recovery credential-generation state machines.
- Explicit `full`, `soul`, and `fs` key-completeness scopes; scoped credential replacement preserves degraded/recovery-only state and cannot satisfy full unlock.
- Coordinated change-password and recovery-credential replacement API/Security UI flows.
- FRK v1 provisioning and per-object crypto helpers.
- Canonical native crypto/key helpers in `anima-corefs`, exposed through the existing `anima-core` PyO3 extension with Rust/Python vector parity and no duplicate Python implementation.
- Focused crypto/recovery regression tests.

## Acceptance

- Password and recovery paths unlock every required root and Soul-domain key.
- No raw key or private profile field appears in the manifest.
- Cross-store interruption tests pass at every durable boundary.
- No live password/recovery endpoint can bypass the active manifest/Soul/FRK credential generation.
- Soul-only completeness requires every Soul root/domain key but forbids FRK slots; CoreFS-only completeness requires every retained FRK but forbids SQLCipher/Soul-domain slots.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 15:45 MYT - Added scoped recovery/keyslot completeness and credential-generation requirements for independently recoverable Soul and CoreFS artifacts.
- 2026-07-12 16:01 MYT - Closed review conflict between full-Core recovery and intentional Soul-only/CoreFS-only credential scopes.
- 2026-07-12 17:34 MYT - Assigned CoreFS crypto ownership to Rust and the existing `anima-core` Python extension boundary.

## Validation

- Commands:
  - `not run yet`
- Changed paths:
  - none
- Notes:
  - Claim before implementation.
