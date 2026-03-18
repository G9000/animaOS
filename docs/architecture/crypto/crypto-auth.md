---
title: Crypto & Authentication
description: Two-layer encryption, session management, key derivation, and domain DEKs
category: architecture
---

# Crypto & Authentication

[Back to Index](README.md)

## Authentication Flow

The system uses a token-based unlock session model:

1. User authenticates with username + password (`bcrypt` hash)
2. Server derives per-domain DEKs from the password using Argon2id
3. Creates an `UnlockSession` with `user_id`, `deks`, and 24h TTL
4. Returns an opaque `unlockToken` (44-char base64)

Every subsequent request must include `x-anima-unlock: <token>` header. The `require_unlocked_user()` dependency validates the token and confirms user_id match.

## Two-Layer Encryption Architecture

### Layer 1: Database Encryption (SQLCipher)

- Full-database encryption using SQLCipher (SQLite extension)
- Key derived via Argon2id + HKDF from user passphrase
- Two modes:
  - **Env-var passphrase**: `ANIMA_CORE_PASSPHRASE` set directly
  - **Unified passphrase**: wrapped in manifest, unwrapped at login

### Layer 2: Field-Level Encryption (AES-256-GCM)

- Sensitive text fields encrypted with per-domain DEKs
- **5 cryptographic domains**: `conversations`, `memories`, `emotions`, `selfmodel`, `identity`
- Each domain uses an independent DEK wrapped with the user's password
- AAD (Additional Authenticated Data) binds ciphertext to `table:user_id:field`
- Prefixes: `enc1:` (no AAD, legacy), `enc2:` (with AAD)
- Helper functions: `ef()` encrypts, `df()` decrypts (used throughout route handlers)

### Key Derivation Chain

```
User Password
  -> Argon2id (per-domain salt from user_keys table)
    -> Domain KEK (Key Encryption Key)
      -> Unwrap DEK (Data Encryption Key, stored as wrapped_dek in user_keys)
        -> AES-256-GCM encrypt/decrypt individual fields

User Password (or ANIMA_CORE_PASSPHRASE)
  -> Argon2id (kdf_salt from manifest.json)
    -> HKDF (info="sqlcipher-key")
      -> SQLCipher database key (hex-encoded)
```

## Key Files

| File | Responsibility |
|------|---------------|
| `services/crypto.py` | Argon2id KDF, AES-256-GCM wrap/unwrap, HKDF for SQLCipher. Constants: `ENCRYPTED_TEXT_PREFIX="enc1"`, `ENCRYPTED_TEXT_PREFIX_AAD="enc2"` |
| `services/data_crypto.py` | Domain-aware `ef()` / `df()` helpers |
| `services/auth.py` | Password hashing/verification (bcrypt) |
| `services/sessions.py` | `UnlockSessionStore` with 24h TTL, DEK memory zeroing on revoke |
| `services/core.py` | Manifest management, SQLCipher salt storage, wrapped key storage |
| `models/user_key.py` | `UserKey` model: per-domain `kdf_salt` + `wrapped_dek` |

## Platform-Specific Notes

- **Windows stack overflow guard** (`crypto.py:40`): Argon2id's C library can overflow the default 1MiB Windows thread stack. `_run_with_large_stack()` spawns a dedicated 8MiB thread for KDF operations.
- **SQLCipher cipher_memory_security** (`session.py:125`): Disabled on Windows due to `STATUS_GUARD_PAGE_VIOLATION`. SQLCipher still zeroes memory on deallocation.
- **DEK memory zeroing** (`sessions.py:171`): On session revoke, `ctypes.memset` attempts to zero DEK bytes. Defense-in-depth since Python `bytes` are immutable.
