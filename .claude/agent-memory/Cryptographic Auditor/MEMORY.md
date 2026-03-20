# Cryptographic Auditor Memory

## Key File Paths
- `apps/server/src/anima_server/services/crypto.py` — KDF, AEAD, key wrapping primitives
- `apps/server/src/anima_server/services/data_crypto.py` — field-level encryption, domain DEK mapping, AAD construction
- `apps/server/src/anima_server/services/vault.py` — vault export/import, envelope encryption, integrity hash
- `apps/server/src/anima_server/services/sessions.py` — DEK session store, zeroization, SQLCipher key cache
- `apps/server/src/anima_server/services/auth.py` — password hashing, DEK wrapping/unwrapping, user creation
- `apps/server/src/anima_server/services/core.py` — manifest management, SQLCipher salt, wrapped key storage
- `apps/server/src/anima_server/db/session.py` — SQLCipher pragma configuration, engine creation
- `apps/server/src/anima_server/db/user_store.py` — registration/auth flows, SQLCipher key generation
- `apps/server/src/anima_server/models/user_key.py` — wrapped DEK storage model
- `apps/server/src/anima_server/main.py` — sidecar nonce middleware (timing-unsafe comparison)

## Audit Findings (2026-03-20)
- See [crypto_audit_findings.md](crypto_audit_findings.md) for detailed findings
- Key issues: timing-unsafe nonce comparison, no AAD on DEK wrapping, legacy migration reuses single DEK across domains, vault integrity hash is redundant with GCM tag, zeroization is best-effort on immutable bytes
