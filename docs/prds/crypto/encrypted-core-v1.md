---
title: Encrypted Core (Product)
author: Julio Caesar
last_edited: 2026-03-14
version: 1
status: draft
scratchboard: v1-encrypted-core
---

# Encrypted Core v1

| Field | Value |
|---|---|
| Author | Julio Caesar |
| Version | 1 |
| Status | Draft |
| Scratchboard | [v1-encrypted-core](../../scratchboard/v1-encrypted-core/brief.md) |
| Created | 2026-03-14 |
| Last edited | 2026-03-14 |

> Personal data should not be readable on disk without the user's passphrase.

## Context

ANIMA's product docs already position the Core as local-first, portable, and encrypted-by-default. The current implementation only partially meets that bar.

Current coverage already in place:

- Vault export and import payloads are encrypted with Argon2id and AES-256-GCM.
- User passwords are stored as Argon2 hashes.
- Each user gets a wrapped DEK that can support per-user data encryption.
- SQLite can be encrypted with SQLCipher when a core passphrase is configured and the dependency is present.

Current gaps still visible in the repo:

- The default local SQLite store can still run in plaintext.
- File-backed user content under `.anima/users/...` is still plain text on disk.
- Core metadata is written as plain JSON.
- Desktop auth state and unlock tokens are persisted in browser `localStorage`.
- Some docs describe the Core as fully encrypted already, which is ahead of shipped behavior.

This version closes the gap between ANIMA's local-first security promise and what actually ships.

## What This Version Delivers

- A passphrase-protected Core setup and unlock flow for the local desktop experience.
- Encrypted-by-default persistence for primary local data storage, including the default Core database and any file-backed personal artifacts that remain outside it.
- Safe migration for existing plaintext local data into the encrypted Core.
- No plaintext reusable session artifacts stored in desktop browser `localStorage`.
- Clear user-facing status and error handling for unlock, migration, and encryption failures.

## What Users See

- On first launch, the app asks the user to create a Core passphrase before personal data is written.
- On existing installs, the app detects plaintext local data and guides the user through a one-time migration.
- On restart, the user unlocks ANIMA with the Core passphrase before personal data becomes available.
- Security and vault settings communicate whether the Core is encrypted, migrated, and portable.
- If unlock fails, the app shows a clear error instead of surfacing server crashes or broken auth behavior.

## Rules

- The Core remains a single portable local directory.
- No cloud account or remote key service is required to unlock local personal data.
- No personal content, secrets, or reusable auth artifacts may be stored in plaintext on disk.
- If any bootstrap metadata remains plaintext, it must contain no personal content or secrets.
- Copying the Core to another machine with the correct passphrase must preserve continuity.
- Existing local users must be migrated without silent data loss.

## Success Metrics

| Metric | Target | How to measure |
|---|---|---|
| Core encryption coverage | 100% of user-private DB content and file-backed personal content unreadable without the passphrase | Automated fixture copies the Core and verifies that direct SQLite and file inspection does not reveal readable user content |
| Plaintext session artifact removal | 0 reusable unlock tokens or serialized user profiles stored in plaintext `localStorage` after login | Desktop integration test and manual storage inspection |
| Migration safety | 100% successful migration for representative plaintext local fixtures with no data loss | Migration integration tests over seeded dev data |
| Portability | Encrypted Core works after copy to a second machine or clean environment with the same passphrase | End-to-end unlock and smoke test after copy/restore |
| Failure clarity | Wrong-passphrase, missing-migration, and missing-encryption-prerequisite states return explicit errors and no HTTP 500 in normal flows | API and desktop smoke tests for negative cases |

## Out of Scope

- Network transport security and provider-side retention guarantees.
- Multi-user or shared-Core workflows.
- Enterprise key management or recovery escrow.
- Provider API key persistence beyond the current runtime-only behavior.
- Broad performance optimization beyond keeping unlock and startup acceptable for local use.

## References

- [Whitepaper](../whitepaper.md)
- [Roadmap](../roadmap.md)
- [Memory System](../memory-system.md)
- [Build Release](../build-release.md)
- [Implementation Plan](../implementation-plan.md)
- [Scratchboard Brief](../../scratchboard/v1-encrypted-core/brief.md)
