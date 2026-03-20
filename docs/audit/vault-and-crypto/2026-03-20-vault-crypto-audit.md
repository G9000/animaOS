# Vault & Crypto Security Audit

**Date**: 2026-03-20
**Auditors**: Codex (GPT-5.4), Claude Crypto Auditor, Claude Security Auditor
**Scope**: SQLCipher integration, key derivation, key wrapping, passphrase handling, encryption at rest, vault import/export, auth flows, trust boundaries, API access control

---

## Architecture Overview

AnimaOS implements a two-layer encryption model:

1. **Database-level encryption** via SQLCipher (AES-256-CBC with HMAC-SHA512)
2. **Field-level encryption** via AES-256-GCM on sensitive text columns, using per-user, per-domain Data Encryption Keys (DEKs)

Key hierarchy:

```
User passphrase
  |
  +-- Argon2id --> KEK (per-domain, per-wrapping-operation salt)
  |     |
  |     +-- AES-256-GCM wraps --> DEK (per-domain, random 32 bytes)
  |
  +-- Argon2id --> master --> HKDF-SHA256 (info="anima-sqlcipher-v1") --> SQLCipher raw key
```

Vault exports use an independent Argon2id derivation from a separate vault passphrase with stronger parameters (t=4, m=128MiB vs t=3, m=64MiB).

---

## Findings Summary

| Severity | Count | Key Themes |
|----------|-------|------------|
| Critical | 0 | -- |
| High | 3 | SQL injection surface, unrestricted DB mutations, debug-mode nonce bypass |
| Medium | 11 | Key lifecycle, weak passwords, brute-force, DEK wrapping, vault handling |
| Low | 6 | Redundant integrity hash, CORS, info leakage, best-effort key zeroing |
| Info | 3 | Windows cipher_memory_security, hex key in closure, export scope |

---

## HIGH Severity

### H1 — SQL Injection via Raw SQL Query Endpoint

- **Source**: Security Auditor
- **File**: `apps/server/src/anima_server/api/routes/db.py:239-266`
- **Description**: The `/api/db/query` endpoint accepts arbitrary SQL. The guard on line 252-253 only checks whether the first word is `SELECT`, `PRAGMA`, or `EXPLAIN`. An attacker can submit `SELECT 1; DROP TABLE users; --` or use subqueries/CTEs that invoke destructive functions. SQLite allows `ATTACH DATABASE` inside a SELECT-like context, and `PRAGMA` itself can mutate state (e.g., `PRAGMA journal_mode`, `PRAGMA key`). The semicolon check is absent, so multi-statement payloads could be passed depending on the SQLAlchemy/driver configuration.
- **Impact**: Data destruction, schema manipulation, potential bypass of encryption pragmas, or reading arbitrary files on the local filesystem via SQLite's file I/O functions.
- **Fix**: (1) Use `sqlite3`'s `set_authorizer` callback to whitelist only `SQLITE_READ` and `SQLITE_SELECT` operations. (2) Reject queries containing semicolons after stripping trailing whitespace. (3) Consider removing this endpoint entirely or placing it behind a development-only flag.
- **Regression test**: Submit `SELECT 1; DELETE FROM users`, `ATTACH DATABASE ...`, `PRAGMA key = ...` and verify they are all rejected.

### H2 — DB Mutation Endpoints Lack Table Restrictions

- **Source**: Security Auditor
- **File**: `apps/server/src/anima_server/api/routes/db.py:283-353`
- **Description**: The `DELETE /api/db/tables/{table_name}/rows` and `PUT /api/db/tables/{table_name}/rows` endpoints call `require_unlocked_session` but allow mutation of system tables like `alembic_version`, `user_keys`, and the `users` table itself. An attacker could modify their own `password_hash` to a known value (bypassing old-password verification in `change_password`), delete or corrupt `user_keys` rows (making the account permanently inaccessible), alter `wrapped_dek` values to inject attacker-controlled key material, or modify `alembic_version` to force re-migration on next startup.
- **Impact**: Authentication bypass, permanent data loss, crypto key corruption.
- **Fix**: (1) Restrict mutable tables to a safe allowlist (exclude `users`, `user_keys`, `alembic_version`). (2) Add a re-authentication gate as a required precondition for mutations. (3) Consider making these endpoints read-only in production.
- **Regression test**: Attempt to UPDATE `users.password_hash` and DELETE from `user_keys` — both should be rejected.

### H3 — Sidecar Nonce Disabled in Debug Builds

- **Source**: Security Auditor
- **File**: `apps/desktop/src-tauri/src/lib.rs:179`
- **Description**: The sidecar nonce is only generated when `!cfg!(debug_assertions)`. In debug builds, the nonce state remains an empty string, and the `SidecarNonceMiddleware` in `main.py:52-53` only enforces when `nonce` is truthy. If a release build is compiled with debug assertions enabled (e.g., `cargo build` without `--release`), the nonce protection silently disappears.
- **Impact**: Any local process can impersonate the desktop app and access all API endpoints with a stolen/brute-forced unlock token.
- **Fix**: Log a prominent warning when the server starts without a sidecar nonce. Consider making the nonce enforcement independent of Rust debug assertions — use an explicit feature flag or environment variable instead.
- **Regression test**: Start the server with `ANIMA_SIDECAR_NONCE=""` and verify a warning is logged. In production builds, verify nonce is always populated.

---

## MEDIUM Severity

### M1 — DEK Material Outlives Logout and Session Expiry

- **Source**: Codex, Security Auditor
- **Files**: `services/sessions.py` (revoke_user, clear), `db/session.py:106-172`, `db/user_store.py:301`
- **Description**: The unlock-session store clears references without wiping the DEKs on `revoke_user()` and `clear()`. In unified-passphrase mode, the raw SQLCipher key is cached globally (`_sqlcipher_key`) and never cleared on logout or password change. Per-user SQLCipher engines are cached after first open and keep the derived key in the engine-connect closure.
- **Exploit path**: Once a user has successfully unlocked the core, any later in-process code execution, debugging hook, or compromised route can access already-unlocked DB engines and residual DEKs without knowing the passphrase again.
- **Fix**: Zero DEKs via `ctypes.memset` in `revoke_user()` and `clear()`, clear the global SQLCipher key on logout/import/password change, and `dispose()` cached per-user engines when lock state is revoked.

### M2 — Vault Import Accepts Attacker-Controlled Argon2 Costs

- **Source**: Codex
- **Files**: `services/vault.py:288-316`, `schemas/vault.py`
- **Description**: `decrypt_string()` trusts `timeCost`, `memoryCostKiB`, `parallelism`, and `keyLength` from the imported vault JSON and feeds them directly into Argon2. There is no upper-bound validation in the schema or service layer.
- **Exploit path**: Importing a malicious `.vault.json` can force extreme memory/CPU usage and hang or crash the server process before decryption fails.
- **Fix**: Enforce strict min/max bounds for KDF parameters (e.g., `timeCost <= 10`, `memoryCostKiB <= 2 GiB`, `parallelism <= 8`) and reject vaults whose parameters exceed supported limits.

### M3 — Weak Password Policy Protects All Encryption Keys

- **Source**: Codex, Security Auditor, Crypto Auditor
- **Files**: `schemas/auth.py:10` (RegisterRequest), `schemas/auth.py:35` (ChangePasswordRequest)
- **Description**: Registration allows 1-character passwords (`min_length=1`). Change-password requires only 6 characters. These passwords wrap all per-domain DEKs and (in unified mode) the raw SQLCipher database key.
- **Impact**: If an attacker gets the manifest and/or per-user DB files, offline guessing becomes materially easier than it should be for the keys protecting the encrypted core.
- **Fix**: Enforce `min_length=8` on registration (matching vault passphrase requirement). Consider `min_length=12` for vault passphrases (offline brute-force target).

### M4 — DB Viewer Decrypts Data With Only the Bearer Token

- **Source**: Codex
- **Files**: `api/routes/db.py:189-239`
- **Description**: The DB viewer has a `/verify-password` endpoint with a comment saying decrypted content should be re-verified first, but the actual read/query endpoints do not require that step and immediately decrypt fields from session DEKs.
- **Exploit path**: Anyone who obtains `x-anima-unlock` during its 24-hour lifetime can dump decrypted rows without knowing the password/passphrase.
- **Fix**: Bind DB-viewer access to a recent password recheck or a separate short-lived reauth token.

### M5 — Timing-Unsafe Sidecar Nonce Comparison

- **Source**: Crypto Auditor
- **File**: `main.py:55`
- **Description**: Python string `!=` comparison short-circuits on the first differing byte. A local attacker can issue requests with progressively correct nonce prefixes and measure response timing to recover the nonce byte-by-byte.
- **Impact**: The nonce is the only mechanism preventing other localhost processes from issuing authenticated API calls. A recovered nonce grants full API access.
- **Fix**: Replace `header_value != nonce` with `not hmac.compare_digest(header_value, nonce)`. One-line fix.

### M6 — DEK Wrapping Uses AES-GCM Without AAD Context Binding

- **Source**: Crypto Auditor
- **File**: `services/crypto.py:155, 183`
- **Description**: `AESGCM(kek).encrypt(iv, dek, None)` — without AAD, a wrapped DEK blob can be copied between domain rows within a single user without detection. The GCM tag only authenticates the DEK plaintext under the KEK+IV, not the intended domain or user.
- **Impact**: Cross-domain key confusion within the same user. Relevant during vault import where `UserKey` records are restored from attacker-controlled JSON.
- **Fix**: Add AAD: `aad = f"dek-wrap:user={user_id}:domain={domain}".encode("utf-8")`. Implement as a versioned migration: new wrappings use AAD (v2), unwrapping tries AAD first, falls back to `None` for legacy records.

### M7 — Legacy Migration Clones One DEK Across All Domains

- **Source**: Crypto Auditor
- **File**: `services/auth.py:193-221`
- **Description**: `_migrate_legacy_single_key()` reuses the same DEK bytes for all five domains (conversations, memories, emotions, selfmodel, identity). Obtaining any one domain key decrypts all domains, voiding the domain separation guarantee.
- **Impact**: Users who registered before multi-domain support have zero isolation between domains. No rotation mechanism exists.
- **Fix**: Implement a key rotation flow that generates fresh independent DEKs per domain, re-encrypts all field-level ciphertext under the new keys, and updates the `user_keys` rows.

### M8 — No Brute-Force Protection on Login

- **Source**: Security Auditor
- **File**: `api/routes/auth.py:101-118`
- **Description**: No rate limiting, account lockout, or exponential backoff on `/api/auth/login`. While Argon2id's computational cost provides some protection (~100-300ms per attempt), an attacker with sustained access can attempt thousands of passwords per hour.
- **Fix**: Add per-IP or per-username rate limiting (e.g., 5 attempts per minute). Implement progressive delay after failed attempts.

### M9 — Vault Export Holds Full Plaintext in Memory

- **Source**: Security Auditor
- **File**: `services/vault.py:132-170`
- **Description**: During `export_vault`, all encrypted database fields are decrypted to plaintext in memory, then the entire plaintext payload is JSON-serialized and re-encrypted with the vault passphrase. The full plaintext of all memories, conversations, emotional signals, and self-model data exists as a single Python string in memory. Python's garbage collector does not guarantee timely deallocation or zeroing.
- **Fix**: Consider streaming encryption to avoid holding the full plaintext in a single buffer. Document that vault export is a privileged operation.

### M10 — Manifest Contains Sensitive Material in Plaintext

- **Source**: Codex, Security Auditor
- **Files**: `services/core.py:62-102`
- **Description**: The `manifest.json` file stores `wrapped_sqlcipher_key`, `sqlcipher_kdf_salt`, `user_index` (username-to-user-id mapping), `core_id`, and `owner_user_id` in plaintext. No file permission restrictions are applied programmatically.
- **Fix**: Set restrictive file permissions (owner-only read/write) when writing the manifest. Consider whether the user index needs to be in plaintext or could be stored as a keyed hash.

### M11 — Vault Import Overwrites All Data Destructively

- **Source**: Security Auditor
- **File**: `services/vault.py:443-475`
- **Description**: `restore_database_snapshot` deletes all rows from all tables before inserting vault data. A crafted vault file could contain a malicious `password_hash` for the imported user, giving the attacker access after re-authentication. The vault also contains `userKeys` with wrapped DEKs — an attacker who knows the vault passphrase can craft keys with known DEKs.
- **Fix**: After import, force password change before allowing normal operations. Warn the user that vault import replaces all data.

---

## LOW Severity

### L1 — Vault Integrity Hash Is Redundant

- **Source**: Crypto Auditor
- **File**: `services/vault.py:249`
- **Description**: SHA-256 hash is computed over the base64-encoded ciphertext and stored alongside it in the same JSON envelope. An attacker who modifies the ciphertext can recompute the hash. The actual tamper detection comes from the GCM authentication tag.
- **Fix**: Rename from `"integrity"` to `"checksum"` or `"corruption_check"` to avoid implying cryptographic tamper resistance.

### L2 — Vault AAD Stored in Envelope Instead of Reconstructed

- **Source**: Crypto Auditor
- **File**: `services/vault.py:311-314`
- **Description**: The AAD value is stored in the same JSON file as the ciphertext. Better practice is to reconstruct the AAD from trusted context (e.g., the envelope's `version` and `payloadVersion` fields).
- **Fix**: Reconstruct AAD during import from `version` and `scope` fields. Eliminate the `aad_b64` field.

### L3 — Health Endpoint Leaks Environment and Provisioning State

- **Source**: Security Auditor
- **File**: `main.py:118-126`
- **Description**: `/health` returns `environment` and `provisioned` fields. The endpoint is nonce-exempt, so any local process can determine whether the Core has been set up and what environment it runs in.
- **Fix**: Remove `environment` and `provisioned` from the health response, or gate them behind authentication.

### L4 — LLM Error Details Leaked to Client

- **Source**: Security Auditor
- **File**: `api/routes/auth.py:50`
- **Description**: Returns `f"AI provider error: {exc}"` which could include internal provider URLs, API key fragments, or model configuration details from provider error responses.
- **Fix**: Return a generic error message and log the full exception server-side.

### L5 — CORS Allows Dev Origins in All Environments

- **Source**: Security Auditor
- **File**: `main.py:25-31`
- **Description**: `http://localhost:1420`, `http://localhost:5173`, and Tauri origins are always allowed regardless of `app_env`. In production mode, dev origins should be restricted.
- **Fix**: In production mode, restrict CORS origins to Tauri origins only.

### L6 — `_zero_dek` Is Best-Effort on Immutable Python Bytes

- **Source**: Crypto Auditor, Security Auditor
- **File**: `services/sessions.py:171-182`
- **Description**: Python `bytes` objects are immutable. The `ctypes.memset` trick overwrites the backing buffer, but Python may have already copied the bytes elsewhere. The code correctly documents this as defense-in-depth.
- **Fix**: Consider using `bytearray` instead of `bytes` for DEKs where possible, since `bytearray` is mutable and can be reliably zeroed.

---

## INFO

### I1 — SQLCipher `cipher_memory_security` Disabled on Windows

- **Source**: Crypto Auditor
- **File**: `db/session.py:128-130`
- **Description**: Causes `STATUS_GUARD_PAGE_VIOLATION` on Windows threads. SQLCipher still zeros sensitive memory on deallocation without this pragma; enabling it adds `mlock`/guard-page hardening. Documented with justification.

### I2 — SQLCipher Hex Key Persists as Python String in Closure

- **Source**: Crypto Auditor
- **File**: `db/session.py:107`
- **Description**: The hex string representation of the SQLCipher key exists as an immutable Python `str` object in the closure's scope. It cannot be zeroed and persists for the engine lifetime (effectively process lifetime). Inherent to the architecture.

### I3 — `scope="memories"` Exports Include `users` and `userKeys` Tables

- **Source**: Codex
- **Files**: `services/vault.py:88-141`
- **Description**: A memories-only export contains auth/key material in addition to memory content. Consider whether `userKeys` belong in `scope="memories"` exports.

---

## Positive Observations

All three auditors independently confirmed these strengths:

1. **KDF selection**: Argon2id with strong parameters (t=3/m=64MiB for session, t=4/m=128MiB for vault) + HKDF-SHA256 domain separation for SQLCipher key
2. **AES-256-GCM** with random 96-bit IVs and proper AAD on field encryption (`table:user_id:field`)
3. **Per-domain DEK isolation** (5 independent keys: conversations, memories, emotions, selfmodel, identity)
4. **Fresh random salt per wrapping operation** via `os.urandom()`
5. **CSPRNG session tokens** — `secrets.token_urlsafe(32)`, 256-bit entropy
6. **Fail-closed design** — server refuses to start when encryption required but no passphrase configured
7. **No plaintext secrets in logs** — grep across entire server codebase found none
8. **Password rehash detection** via `check_needs_rehash()` for transparent parameter upgrades
9. **Path traversal protection** — `sanitize_relative_path` in vault import rejects absolute paths and `..` components
10. **Tauri capabilities** — minimal capability set, no filesystem/shell/process access exposed to webview

---

## Recommended Fix Priority

| Priority | Findings | Effort | Notes |
|----------|----------|--------|-------|
| **P0 — Now** | H1, H2, M5 | Small | SQL injection surface, table allowlist, timing-safe nonce (1 line) |
| **P1 — Soon** | M3, M8, M2 | Small | Password min length, rate limiting, KDF param bounds |
| **P2 — Next** | M1, M4, M6, H3, M10 | Medium | DEK zeroing, DB viewer re-auth, AAD on wrapping, nonce enforcement, manifest permissions |
| **P3 — Plan** | M7, M11, M9, L1-L6 | Large | Key rotation, post-import reset, streaming export, cosmetic/hardening |

---

## Open Questions

- Does the desktop always run with `ANIMA_SIDECAR_NONCE`? If not, the unlock-token findings (M4) become more serious.
- OS-level filesystem permissions on `.anima/` were not verified; local read access materially affects brute-force and metadata-leakage findings.
- No mechanism exists to migrate already-open SQLCipher engines back to a locked state; if one exists elsewhere, it would reduce M1.
