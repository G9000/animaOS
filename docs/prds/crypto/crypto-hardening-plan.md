---
title: "Cryptographic Hardening — Implementation Plan"
version: 0.1
author: Julio Caesar
date: 2026-03-17
status: draft
depends_on: [portable-core thesis, succession-protocol thesis]
scope: apps/server
tags: [cryptography, implementation, plan]
---

# Cryptographic Hardening — Implementation Plan

| Field       | Value                                                             |
| ----------- | ----------------------------------------------------------------- |
| Author      | Julio Caesar                                                      |
| Version     | 0.1                                                               |
| Status      | Draft                                                             |
| Created     | 2026-03-17                                                        |
| Last edited | 2026-03-17                                                        |
| Scope       | `apps/server`                                                     |
| Depends on  | [Portable Core](../thesis/portable-core.md), [Succession Protocol](../thesis/succession-protocol.md), [Encrypted Core v1](./encrypted-core-v1.md) |

---

## Overview

A full cryptographic audit of AnimaOS revealed three critical security defects and seven significant architectural gaps between what the thesis documents promise and what the codebase delivers. This plan phases the fixes and improvements into six stages with explicit dependencies, so that each phase leaves the system in a working, tested state. Critical security fixes come first. New capabilities build on top only after the foundation is sound.

---

## Principles

- **Fix security defects before adding new capabilities.** The three critical findings (SQLCipher KDF, filesystem boundary, AAD enforcement) are blocking issues that undermine the existing encryption promise.
- **Each phase must leave the system in a working, tested state.** No phase may be "half-applied." All 131 existing tests must pass after every phase, plus new tests specific to that phase.
- **Migrations must be reversible or have explicit rollback plans.** Crypto migrations touch irreplaceable data. Every migration that re-encrypts data must be tested against fixture databases and support interruption/resumption.
- **No rolling your own crypto — use audited libraries only.** `cryptography` (pyca), `argon2-cffi`, `sqlcipher3`, `pycryptodome` (for Shamir's). No hand-rolled KDFs, no custom block cipher modes.

---

## Phase 0: Critical Fixes (Week 1-2)

These are security defects or thesis-promise gaps. They must be resolved before any new capabilities are added.

### 0.1 Derive SQLCipher key via Argon2id

**Current state**: `db/session.py` line 95-96 passes the raw passphrase directly to `PRAGMA key = '{escaped}'`. SQLCipher then runs its internal PBKDF2-HMAC-SHA512 (256k iterations) on top. This means two KDFs run in series — the user has no control over the parameters of the one that actually protects the database file, and the total derivation time is unpredictable.

**Target state**: `passphrase -> Argon2id(salt) -> HKDF-SHA256("sqlcipher") -> 32-byte raw key -> PRAGMA key = "x'<hex>'"`. The raw key mode tells SQLCipher to skip its internal PBKDF2 entirely. We control the full KDF chain.

**Files**:
- `apps/server/src/anima_server/db/session.py` — `_set_sqlcipher_key` listener
- `apps/server/src/anima_server/config.py` — store KDF salt in manifest or derive deterministically
- `apps/server/src/anima_server/services/crypto.py` — add `derive_sqlcipher_key()` using `derive_argon2id_key()` + HKDF

**Migration**: Use SQLCipher's `PRAGMA rekey = "x'<new_hex>'"` to re-key existing encrypted databases. This is atomic — SQLCipher handles the re-encryption internally. The old passphrase-mode key must be derived first to open the database, then the new raw key applied via `PRAGMA rekey`.

**Tests**: Existing test suite must pass. Add a unit test that opens a database with a raw hex key and verifies reads/writes. Add a migration test that re-keys a fixture database from passphrase mode to raw key mode.

**Effort**: 1-2 days

### 0.2 Wire AAD into all field-level encryption

**Current state**: `data_crypto.py` supports `table` and `field` kwargs for AAD binding, and `crypto.py` already handles the `enc1`/`enc2` prefix distinction. However, an audit of all `ef()` call sites shows that none of them pass `table` or `field` arguments:

```
ef(user_id, summary)                         # episodes.py
ef(user_id, evidence_text)                    # claims.py
ef(user_id, content)                          # memory_store.py, self_model.py
ef(user_id, summary_text)                     # compaction.py
ef(thread.user_id, summary_text)              # compaction.py
ef(user_id, evidence)                         # emotional_intelligence.py
ef(user_id, value)                            # session_memory.py
ef(ctx.user_id, content.strip())              # tools.py
ef(user_id, parsed["persona_update"])         # inner_monologue.py
```

All of these produce `enc1`-prefixed ciphertext with no AAD. An encrypted memory blob can be moved between tables or users without detection.

**Target state**: Every `ef()` call passes `table` and `field`. Every `df()` call passes the same context. All new ciphertext uses `enc2` prefix with AAD bound to `"{table}:{user_id}:{field}"`.

**Files**:
- `services/agent/episodes.py` — `ef(user_id, summary, table="memory_episodes", field="summary")`
- `services/agent/claims.py` — all `ef()` calls with `table="memory_items"` or `table="memory_claim_evidence"`
- `services/agent/memory_store.py` — all `ef()` calls with appropriate table/field
- `services/agent/self_model.py` — `table="self_model_blocks"`, `field="content"`
- `services/agent/emotional_intelligence.py` — `table="emotional_signals"`, field per column
- `services/agent/compaction.py` — `table="agent_messages"`, `field="content_text"`
- `services/agent/persistence.py` — `table="agent_messages"`, `field="content_text"`
- `services/agent/session_memory.py` — `table="session_notes"`, `field="value"`
- `services/agent/tools.py` — per-tool table/field
- `services/agent/inner_monologue.py` — `table="self_model_blocks"`, `field="content"`
- `services/vault.py` — vault re-encryption during import must pass context

**Migration**: Write a one-time migration script that:
1. Iterates all encrypted columns in all tables
2. For each `enc1`-prefixed value: decrypt with no AAD, re-encrypt with proper AAD, write back as `enc2`
3. Must be resumable — track progress per table/row so a crash mid-migration does not require restart from zero
4. Must run while the Core is unlocked (DEK in memory)

**Risk**: This migration touches every encrypted value in the database. For a large Core with thousands of memories and conversation messages, it could be slow. Batch the migration with progress reporting. Allow interruption and resumption by tracking the last-processed row ID per table.

**Tests**: Unit tests for AAD round-trip (encrypt with AAD, decrypt with same AAD succeeds, decrypt with wrong AAD fails). Migration test against a fixture database with `enc1` values.

**Effort**: 1-2 days for call sites, 1 day for migration script

### 0.3 Seal the filesystem boundary

**Current state**: `services/storage.py` and `db/session.py` write per-user data to `users/{id}/` directories. The user database itself (`anima.db`) lives at `users/{id}/anima.db`. The Portable Core thesis (Section 5.3) requires all personal data to live inside the encrypted database — no loose files outside the sealed boundary.

**Action items**:
1. Audit all code paths that call `get_user_data_dir()` — verify what files exist outside the database
2. Move any remaining file-based personal data into SQLite tables
3. Mark `chroma/` directories and any embedding caches as derived/disposable in the manifest
4. Document which files in `.anima/` are canonical vs derived, per Section 5.4 of the thesis

**Files**:
- `services/storage.py` — `get_user_data_dir()`
- `db/session.py` — `get_user_database_path()`
- `db/user_store.py` — any file-based user data

**Tests**: Add a test that creates a user, runs a conversation, and verifies no personal data exists outside encrypted database files.

**Effort**: 2-3 days

### 0.4 SQLCipher PRAGMA hardening

**Current state**: `db/session.py` lines 96-98 set `PRAGMA key`, `journal_mode = WAL`, and `busy_timeout = 5000`. No cipher-specific hardening PRAGMAs are set.

**Target state**: After `PRAGMA key`, add:
```sql
PRAGMA cipher_page_size = 4096;
PRAGMA cipher_memory_security = ON;
```

Once Phase 0.1 is complete (raw key mode), also set `PRAGMA kdf_iter = 0` to disable SQLCipher's internal PBKDF2, since our own Argon2id handles key derivation.

**Files**:
- `apps/server/src/anima_server/db/session.py` — `_set_sqlcipher_key` listener

**Tests**: Existing test suite passes. Add a smoke test that opens an encrypted database with hardened PRAGMAs and verifies table creation and data round-trip.

**Effort**: 0.5 day

---

## Phase 1: Key Hierarchy Upgrade (Week 3-4)

Depends on: Phase 0 complete

### 1.1 Per-domain DEKs

**Current state**: Each user has a single DEK stored in `user_keys` (one row per user, `unique=True` on `user_id`). This single DEK encrypts all field-level data across all tables — memories, conversations, emotions, self-model, identity.

**Target state**: Replace single DEK with domain-specific DEKs:
- `DEK_conversations` — `agent_messages`, `agent_threads`
- `DEK_memories` — `memory_items`, `memory_episodes`, `memory_daily_logs`, `memory_claim_evidence`
- `DEK_emotions` — `emotional_signals`
- `DEK_selfmodel` — `self_model_blocks`, `session_notes`
- `DEK_identity` — `core_identity_keys` (Phase 2), succession keys

**Schema changes**:
- Add `domain` column to `user_keys` table (VARCHAR, nullable, default `NULL` for legacy single-DEK rows)
- Remove `unique=True` constraint on `user_id` in `UserKey` model, replace with unique constraint on `(user_id, domain)`
- `models/user_key.py` — update model definition

**Code changes**:
- `services/sessions.py` — session store holds `dict[str, bytes]` mapping domain to DEK instead of single `bytes`
- `services/data_crypto.py` — `get_active_dek(user_id)` becomes `get_active_dek(user_id, domain)`, with domain resolved from `table` parameter
- Add domain-to-table mapping in `data_crypto.py`:
  ```python
  TABLE_DOMAIN_MAP = {
      "agent_messages": "conversations",
      "agent_threads": "conversations",
      "memory_items": "memories",
      "memory_episodes": "memories",
      ...
  }
  ```

**Migration**: The existing single DEK becomes the `DEK_memories` domain key (the most common). Generate four new random DEKs for the other domains, wrap each with the existing KEK. Re-encrypt data in non-memory tables with the appropriate new domain DEK. This migration must be atomic per table — if it fails mid-table, that table rolls back to the old DEK.

**Effort**: 2-3 days

### 1.2 Unified passphrase model

**Current state**: The SQLCipher passphrase (`ANIMA_CORE_PASSPHRASE` in `config.py`) and the user login password (used to derive KEK for DEK wrapping in `auth.py`) are separate secrets. The SQLCipher passphrase protects the database-at-rest layer. The user password protects the field-level layer. These are independent key paths.

**Target state**: Single user passphrase derives everything:
1. `passphrase -> Argon2id(salt_master) -> Master KEK`
2. `Master KEK -> HKDF("sqlcipher") -> SQLCipher raw key` (database-at-rest)
3. `Master KEK -> AES-GCM-wrap -> domain DEKs` (field-level)

This eliminates the `ANIMA_CORE_PASSPHRASE` environment variable. The Core passphrase and the user password become the same secret. The first user to create an account on a fresh Core sets the Core passphrase implicitly.

**Design decision needed**: Multi-user Cores. If multiple users share a Core (not currently supported but not explicitly ruled out), each user's password would derive their own domain DEKs, but the SQLCipher key must be shared. Options: (a) single-user Core (simplest, matches current behavior), (b) Core passphrase derived from first user, additional users get field-level encryption only. Document the decision in an ADR.

**Files**:
- `services/crypto.py` — add HKDF derivation step
- `db/session.py` — accept derived raw key instead of raw passphrase
- `services/auth.py` — `authenticate_user()` derives Master KEK, passes derived SQLCipher key to session layer
- `config.py` — deprecate `ANIMA_CORE_PASSPHRASE`

**Effort**: 1-2 days (after 0.1 and 1.1)

### 1.3 Argon2id parameter tuning

**Current state**: `crypto.py` uses `time_cost=3`, `memory_cost=64MiB`, `parallelism=1`. `auth.py` uses the same via `PASSWORD_HASHER`. These are reasonable but conservative.

**Target state**:
- Login/field-level: `time_cost=3`, `memory_cost=64MiB`, `parallelism=4`
- Vault export: `time_cost=4`, `memory_cost=128MiB`, `parallelism=4` (vaults deserve stronger derivation — they may be stored offline for years)
- Succession: `time_cost=4`, `memory_cost=128MiB`, `parallelism=4`

**Code changes**:
- `services/crypto.py` — update defaults, add `VAULT_ARGON2_*` constants
- `services/auth.py` — `PASSWORD_HASHER` already has `check_needs_rehash`; verify it triggers re-hash on next login when parameters change

**Effort**: 0.5 day

---

## Phase 2: Cryptographic Identity (Week 5-6)

Depends on: Phase 1 (needs domain DEKs for `DEK_identity`)

### 2.1 Core identity keypair

**Purpose**: Give each Core a verifiable cryptographic identity. The keypair enables vault signing (2.2), integrity attestation (2.3), and future capabilities like signed outputs and DIDs.

**Implementation**:
- Generate Ed25519 keypair at user creation time (alongside DEK generation in `auth.py:create_user()`)
- Private key encrypted with `DEK_identity` and stored in a new table or in `user_keys` with `domain="identity"`
- Public key stored unencrypted in the Core manifest (it is not secret)
- Use `cryptography.hazmat.primitives.asymmetric.ed25519` — no new dependencies

**New table** (or extend `user_keys`):
```sql
CREATE TABLE core_identity_keys (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    public_key TEXT NOT NULL,          -- base64-encoded Ed25519 public key
    encrypted_private_key TEXT NOT NULL, -- encrypted with DEK_identity
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    rotated_at DATETIME,
    UNIQUE(user_id)
);
```

**Files**:
- `services/auth.py` — keypair generation in `create_user()`
- `services/crypto.py` — `sign_bytes()`, `verify_signature()` wrappers
- `models/` — new model or extended `UserKey`

**Effort**: 2-3 days

### 2.2 Vault signing

**Current state**: `services/vault.py` exports vault envelopes with `VAULT_VERSION = 2`. The envelope contains encrypted payload, KDF params, and integrity hash. No cryptographic signature.

**Target state**: On export, sign the vault ciphertext with the Core's Ed25519 private key. Include `signature` (base64) and `signing_public_key` (base64) in the vault envelope JSON.

On import:
1. If `signature` field is present, verify it against `signing_public_key`
2. If verification fails, warn the user but do not refuse import (the key may have rotated, or the vault may come from a different Core version)
3. If `signature` field is absent, treat as unsigned legacy vault — no warning

**Files**:
- `services/vault.py` — `export_vault()` and `import_vault()` functions

**Effort**: 1-2 days

### 2.3 Core integrity attestation

**Purpose**: Detect tampering with the Core while it was locked. If someone modifies the encrypted database file while the Core is not running, the next unlock should detect it.

**Implementation**:
1. At lock/logout: compute a Merkle root over critical tables by hashing sorted row content
2. Sign the Merkle root with the Ed25519 identity key
3. Store the signed attestation in the manifest file (not inside the database it is attesting)

**Critical tables**: `self_model_blocks`, `memory_items`, `user_keys`, `emotional_signals`

**Behavior on mismatch**: Warn the user. Do not refuse to open the Core. The user decides whether to proceed. Log the mismatch with details about which table's hash differs.

**Files**:
- New: `services/integrity.py` — `compute_attestation()`, `verify_attestation()`
- `services/core.py` — call attestation on lock and verify on unlock
- `services/storage.py` — manifest read/write for attestation data

**Effort**: 2-3 days

---

## Phase 3: Vault Hardening (Week 7)

Depends on: Phase 2 (vault signing must be in place)

### 3.1 Vault compression

**Current state**: `vault.py` serializes the payload as JSON, then encrypts. No compression.

**Target state**: Compress with zstd before encryption: `compressed = zstd.compress(json_bytes)`. Add `"compression": "zstd"` to the vault envelope.

**Backward compatibility**: On import, if `compression` field is absent or `"none"`, assume uncompressed. This means new code can read old vaults, but old code cannot read compressed vaults (acceptable — version bump handles this).

**New dependency**: `zstandard` (Python binding for zstd, well-maintained, MIT licensed)

**Effort**: 0.5 day

### 3.2 Vault sequence numbers

**Purpose**: Detect replay attacks where an attacker replaces a newer Core with an older vault export.

**Implementation**:
- Add `vault_export_counter: int` to the Core manifest (starts at 0)
- Increment on every vault export
- Include counter value in vault envelope as `"sequence": N`
- On import: reject vaults where `sequence < manifest.vault_export_counter` with a clear error message explaining why

**Files**:
- `services/vault.py` — export increments counter, import checks counter
- `services/storage.py` — manifest schema update

**Effort**: 0.5 day

### 3.3 Vault envelope v2 spec

**Purpose**: Formalize the vault format as a documented JSON schema, per thesis Section 8.1.

**Deliverable**: A JSON Schema document at `docs/specs/vault-envelope-v3.json` covering:
- `version` (integer, required)
- `scope` (string: `"full"` | `"memories_only"` | `"anonymized"`)
- `compression` (string: `"none"` | `"zstd"`)
- `signature` (string, base64, optional)
- `signing_public_key` (string, base64, optional)
- `sequence` (integer)
- `kdf_params` (object: salt, time_cost, memory_cost, parallelism)
- `ciphertext` (string, base64)
- `integrity_hash` (string, SHA-256 of ciphertext)

**Effort**: 1 day

---

## Phase 4: Privacy-Preserving Inference (Week 8-10)

Depends on: Phase 1 (needs domain DEKs for sensitivity-based filtering). Can run in parallel with Phases 2-3.

### 4.1 Context sanitization pipeline

**Purpose**: When ANIMA sends context to a remote LLM, minimize PII exposure. This addresses the "inference transit" boundary described in thesis Section 3.1.

**Implementation**:
- New service: `services/agent/context_sanitizer.py`
- Pipeline stages:
  1. **NER-based name replacement**: Replace real names with consistent pseudonyms (e.g., "Alice" -> "Person-A") using a lightweight NER model or regex patterns
  2. **Date generalization**: Replace specific dates with relative references ("March 15, 2026" -> "recently")
  3. **Sensitivity filtering**: Skip memory items above a configurable sensitivity threshold
- Runs between memory retrieval (`embeddings.py:hybrid_search()`) and prompt assembly (`memory_blocks.py:build_relevant_memories_block()`)
- Configurable per-user: `sanitization_level` setting (`off`, `light`, `aggressive`)
- Default: `off` for local inference (Ollama), `light` for remote inference (OpenRouter)

**Files**:
- New: `services/agent/context_sanitizer.py`
- `services/agent/memory_blocks.py` — call sanitizer before injecting memories
- `services/agent/service.py` — pass provider locality info to sanitizer

**Effort**: 1-2 weeks

### 4.2 TEE-aware provider selection

**Current state**: Provider configuration in `config.py` has no concept of trust levels or TEE support.

**Target state**:
- Add `tee_mode` field to provider configuration (enum: `none`, `tee_available`, `tee_required`)
- Document which OpenRouter endpoints currently support TEE inference (e.g., NVIDIA Confidential Computing)
- Display a privacy indicator in the frontend: local / remote / TEE-protected

**Files**:
- `config.py` — provider configuration schema
- Frontend: privacy indicator component (scope TBD)

**Effort**: 2-3 days for backend config + API, frontend TBD

### 4.3 TEE attestation verification (stretch goal)

**Purpose**: Verify Intel TDX or NVIDIA Confidential Computing attestation reports before sending sensitive context to a remote provider.

**Status**: Depends on Python attestation verification SDKs maturing. As of 2026-03, the tooling is early-stage. This item is tracked but not committed.

**Effort**: 2-4 weeks if SDKs are available; deferred otherwise

---

## Phase 5: Advanced Succession (Week 11-13)

Depends on: Phase 1 (domain DEKs), Phase 2 (identity keypair)

### 5.1 Shamir's Secret Sharing

**Current state**: The succession protocol (thesis Section 2) uses a two-key architecture where a single succession passphrase wraps the DEK. A single compromised beneficiary can claim the Core.

**Target state**: Multi-guardian succession using Shamir's Secret Sharing. M-of-N guardians must collaborate to reconstruct the succession key.

**Implementation**:
- Use `shamir-mnemonic` (SLIP-39) for human-friendly mnemonic share encoding, or `PyCryptodome` `Crypto.Protocol.SecretSharing`
- New table: `succession_guardians`
  ```sql
  CREATE TABLE succession_guardians (
      id INTEGER PRIMARY KEY,
      config_id INTEGER NOT NULL REFERENCES succession_configs(id) ON DELETE CASCADE,
      guardian_name TEXT NOT NULL,
      guardian_contact TEXT,
      share_index INTEGER NOT NULL,
      share_hash TEXT NOT NULL,  -- hash of the share for verification, not the share itself
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
  ```
- `succession_configs` gains `threshold` (M) and `total_shares` (N) columns
- API: `POST /api/succession/configure` accepts `guardians[]` array and `threshold`
- At setup: generate random succession secret, split into N shares via Shamir's, wrap DEKs with the succession secret, return shares as mnemonic word sequences to display once
- At claim: M guardians provide their shares, reconstruct the succession secret, unwrap DEKs

**Files**:
- `services/succession.py` (new or extended)
- `api/routes/succession.py` — updated endpoints
- `models/` — new `SuccessionGuardian` model

**Effort**: 1 week crypto implementation, 1 week API/UX

### 5.2 Cryptographic succession scopes

**Current state**: The succession protocol's `transfer_scope` controls what data is copied, but the single DEK means the beneficiary gets access to everything cryptographically — scope filtering is done by deleting rows, not by withholding keys.

**Target state**: With domain DEKs, the succession KEK wraps only the DEKs corresponding to the configured scope:
- `full`: wraps all domain DEKs
- `memories_only`: wraps `DEK_memories` + `DEK_selfmodel`
- `anonymized`: wraps `DEK_selfmodel` only

Data in domains whose DEKs are not transferred becomes cryptographically inaccessible to the beneficiary, not just deleted. This is defense-in-depth — the row deletion still happens, but the cryptographic boundary provides a second guarantee.

**Files**:
- `services/succession.py` — scope-aware DEK wrapping
- `services/vault.py` — vault export respects domain DEK availability

**Effort**: 1-2 days (after domain DEKs are implemented)

### 5.3 Inheritance chains

**Purpose**: Allow the `claimed` state to cycle back to `active` with new succession configuration, so a beneficiary can set up their own succession plan for the inherited Core.

**Implementation**: State machine update in `succession_configs` — `claimed` state transitions to `active` when the new owner configures succession. No new cryptographic work needed.

**Effort**: 0.5 day

---

## Dependency Graph

```
Phase 0 (Critical Fixes)
    ├── 0.1 SQLCipher KDF ─────────────────────────────┐
    ├── 0.2 AAD Enforcement ───────────────────────────┐│
    ├── 0.3 Filesystem Boundary                        ││
    └── 0.4 PRAGMA Hardening ◄── depends on 0.1       ││
                                                       ││
Phase 1 (Key Hierarchy) ◄── depends on Phase 0        ││
    ├── 1.1 Per-domain DEKs ◄── depends on 0.2 ───────┘│
    ├── 1.2 Unified passphrase ◄── depends on 0.1, 1.1 ┘
    └── 1.3 Argon2id tuning
                │
                ├──────────────────────────────────────────┐
                │                                          │
Phase 2 (Crypto Identity) ◄── depends on 1.1              │
    ├── 2.1 Core keypair                                   │
    ├── 2.2 Vault signing ◄── depends on 2.1               │
    └── 2.3 Integrity attestation ◄── depends on 2.1       │
                │                                          │
Phase 3 (Vault Hardening) ◄── depends on 2.2              │
    ├── 3.1 Compression                                    │
    ├── 3.2 Sequence numbers                               │
    └── 3.3 Envelope v2 spec                               │
                                                           │
Phase 4 (Privacy Inference) ◄── depends on 1.1 ───────────┘
    ├── 4.1 Context sanitization       (can run parallel to Phase 2-3)
    ├── 4.2 TEE provider selection
    └── 4.3 TEE attestation (stretch)

Phase 5 (Advanced Succession) ◄── depends on 1.1 + 2.1
    ├── 5.1 Shamir's SSS
    ├── 5.2 Crypto scopes ◄── depends on 1.1
    └── 5.3 Inheritance chains
```

---

## Testing Strategy

Each phase adds tests on top of the existing 131-test suite. No phase may reduce the passing test count.

### Per-phase test requirements

| Phase | New test categories |
| ----- | ------------------- |
| 0.1   | Raw key mode open/read/write. Re-key migration round-trip. |
| 0.2   | AAD encrypt/decrypt round-trip. Wrong-AAD rejection. `enc1` -> `enc2` migration on fixture DB. |
| 0.3   | Post-conversation filesystem audit — no personal data outside encrypted DB files. |
| 0.4   | Encrypted DB smoke test with hardened PRAGMAs. |
| 1.1   | Multi-DEK wrap/unwrap. Domain resolution from table name. Single-DEK -> multi-DEK migration. |
| 1.2   | Unified passphrase derives both SQLCipher key and KEK. Password change re-wraps all domain DEKs atomically. |
| 2.1   | Ed25519 keypair generation, sign/verify round-trip. |
| 2.2   | Vault export includes signature. Import verifies signature. Legacy unsigned vault imports without error. |
| 2.3   | Tamper detection: modify DB while locked, verify attestation catches it on next unlock. |
| 3.1   | Compressed vault round-trip. Uncompressed legacy vault still imports. |
| 3.2   | Sequence number enforcement. Reject stale vault. Accept current-or-newer vault. |
| 4.1   | Sanitizer replaces names. Sanitizer generalizes dates. Round-trip with sanitization on/off. |
| 5.1   | Shamir split/reconstruct round-trip. M-of-N threshold enforcement. |
| 5.2   | Scoped succession: `memories_only` claim cannot decrypt conversation data. |

### Fixture databases

Create fixture encrypted databases at each migration boundary:
- `fixtures/plaintext.db` — unencrypted baseline
- `fixtures/enc1_single_dek.db` — current production state (enc1, single DEK)
- `fixtures/enc2_single_dek.db` — after Phase 0.2 (enc2 with AAD)
- `fixtures/enc2_multi_dek.db` — after Phase 1.1 (domain DEKs)

Each migration test loads the previous fixture and verifies the migration produces the next fixture's expected state.

---

## Risk Register

| Risk | Impact | Likelihood | Mitigation |
| ---- | ------ | ---------- | ---------- |
| AAD migration corrupts existing encrypted data | Critical — data loss | Low | Test with copies of real databases. Make migration resumable. Backup before migration. Provide rollback script that re-encrypts enc2 back to enc1. |
| AAD migration is slow for large Cores | Medium — poor UX | Medium | Batch the migration with progress callback. Allow interruption and resumption via row-ID tracking. |
| SQLCipher re-key fails mid-operation | Critical — locked out | Very low | SQLCipher's `PRAGMA rekey` is atomic. Test with simulated power loss (kill process mid-rekey, verify old key still works). |
| Per-domain DEKs increase unlock latency | Low — UX | Very low | 5 AES unwraps at <1ms each = ~5ms total. Negligible. |
| Unified passphrase breaks multi-user Cores | Medium — architecture | Low | Document single-user-per-Core as the supported model. Multi-user is not currently used. |
| Context sanitization degrades LLM response quality | Medium — UX | Medium | Default to `off` for local inference. Make configurable. A/B test with representative conversations. |
| Shamir share loss locks out all guardians | Critical — succession fails | Low | Require N > M + 1 (redundancy). Recommend guardians store shares in separate physical locations. |
| zstd dependency adds supply chain risk | Low — security | Very low | `zstandard` is widely used, MIT licensed, maintained by Facebook/Meta. Pin version. |

---

## Success Criteria

| Phase | Criteria |
| ----- | -------- |
| **0** | No plaintext secrets or personal data outside the encrypted boundary. All field encryption uses AAD (`enc2` prefix). SQLCipher uses Argon2id-derived raw key. Hardened PRAGMAs active. |
| **1** | Per-domain DEKs working. Password change re-wraps all domain DEKs atomically. Single unified passphrase controls both database-at-rest and field-level encryption. |
| **2** | Core has a verifiable Ed25519 identity. Vault exports are signed. Tamper detection fires on unlock when database was modified while locked. |
| **3** | Vault compression reduces export size. Replay detection via sequence numbers rejects stale vaults. Vault envelope format is formally specified. |
| **4** | Privacy indicator visible in UI. Context sanitization active for remote providers. User can configure sanitization level. |
| **5** | Multi-guardian succession working end-to-end with Shamir's Secret Sharing. Transfer scope enforced cryptographically via domain DEK selection. Inheritance chains allow re-configuration after claim. |

---

## Future Extensions (Not Scoped)

These items are mentioned in the thesis documents or came out of the audit but are not committed to a timeline:

- **Signed LLM outputs**: Core signs its own generated text with the identity key, creating a verifiable chain of authorship.
- **Decentralized Identifiers (DIDs)**: Publish the Core's public key as a DID document for cross-system identity verification.
- **Social recovery**: Use guardian shares for passphrase recovery (not just succession), enabling recovery without destroying cryptographic mortality for the general case.
- **Hidden volumes**: Plausible deniability — a secondary passphrase opens a decoy Core with innocuous data, while the real Core remains hidden.
- **Vault forward secrecy**: Ephemeral key component mixed into vault encryption so that passphrase compromise does not retroactively compromise old vault exports (thesis Section 3.6).
- **Hardware key support**: Allow Core passphrase to be augmented or replaced by a hardware security key (YubiKey, FIDO2) for users who want stronger authentication without longer passphrases.
