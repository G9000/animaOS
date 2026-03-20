# Crypto Audit Findings Summary (2026-03-20)

## Confirmed Issues
1. **Medium** — Sidecar nonce comparison uses `!=` (timing-unsafe) in main.py:55
2. **Medium** — DEK wrapping uses AESGCM with `aad=None` — no context binding (crypto.py:155)
3. **Medium** — Legacy migration clones single DEK across all domains (auth.py:193-221)
4. **Low** — Vault integrity hash (SHA-256 of ciphertext) is redundant with GCM auth tag
5. **Low** — Vault import AAD recovered from envelope itself — attacker-controlled if envelope is modified
6. **Info** — Zeroization of Python `bytes` is best-effort via ctypes.memset (sessions.py:171-182)
7. **Info** — cipher_memory_security disabled on Windows (session.py:129)
8. **Info** — SQLCipher key stored as hex string in closure — lives in Python heap as `str`

## Positive Observations
- Argon2id with strong parameters (t=3, m=64MiB, p=4; vault uses t=4, m=128MiB)
- Fresh random salt per wrapping operation
- Fresh random 12-byte IV per encryption
- HKDF domain separation for SQLCipher key derivation
- Per-domain DEK architecture with 5 independent domains
- Vault uses stronger KDF parameters than session-level encryption
- AAD binding implemented for field-level encryption (table:user_id:field)
- Unlock token uses secrets.token_urlsafe(32)
- No plaintext secrets found in logging
