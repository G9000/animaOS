# Crypto Audit Findings Summary (2026-03-28)

## Confirmed Issues (Updated)
1. **High** — Recovery-wrapped DEKs not re-wrapped on password change; will cause data loss when key rotation is implemented (auth.py:274-308, routes/auth.py:198-228)
2. **High** — Vault export `_decrypt_field_value()` calls `decrypt_text_with_dek()` without AAD, causing GCM auth failure for `enc2`-prefixed ciphertext (vault.py:126-143)
3. **Medium** — Legacy AAD fallback in `unwrap_dek()` bypasses domain binding — attacker with DB write access can swap DEK ciphertexts between domains (crypto.py:202-210)
4. **Medium** — Account deletion uses `shutil.rmtree()` without clearing manifest keys or SQLCipher cache; whitepaper overclaims "cryptographic mortality" (user_store.py:214-216, routes/users.py:77-91)
5. **Medium** — Vault import reads AAD from envelope's `aad_b64` field instead of computing it independently; binding is weaker than intended (vault.py:307-374)
6. **Low** — Unlock token stored in sessionStorage (api.ts:4-25); accessible to WebView XSS
7. **Low** — Redundant SHA-256 checksum in vault envelope (vault.py:278,297-300)
8. **Info** — Zeroization of Python `bytes` is best-effort via ctypes.memset (sessions.py:205-214) — correctly documented
9. **Info** — SQLCipher does not set explicit HMAC algorithm or KDF iter (irrelevant with raw key mode) (session.py:121-133)

## Resolved Since Prior Audit (2026-03-20)
- Sidecar nonce comparison now uses `hmac.compare_digest()` (was `!=`) — FIXED
- DEK wrapping now uses AAD (`dek-wrap:user={user_id}:domain={domain}`) — FIXED (but legacy fallback remains, see finding 3)

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
- BIP39 12-word recovery phrase (128-bit entropy) from standard library
- Login rate limiting (5 attempts per 60s per username)
- Vault KDF parameter caps prevent DoS via crafted vault files
- Manifest file permissions set to 0o600 on non-Windows
- Vault version migration chain with downgrade rejection
