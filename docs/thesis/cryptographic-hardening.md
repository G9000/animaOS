---
title: "Cryptographic Hardening: From Sound Foundations to Complete Architecture"
author: Julio Caesar
version: 0.1
date: 2026-03-17
tags: [cryptography, encryption, key-management, identity, privacy, thesis]
---

# Cryptographic Hardening: From Sound Foundations to Complete Architecture

_A companion thesis to [The Portable Core](portable-core.md). That document established the philosophy — cryptographic mortality, passphrase sovereignty, the key chain, the four encryption surfaces. This document addresses what was missed, what was assumed but never enforced, and what the architecture needs to become complete._

> **Note:** This thesis is a living document. Some sections describe settled improvements with clear implementation paths. Others describe constructions that are architecturally sound but not yet scheduled. A few describe open research questions. Each section states its confidence level. Expect this document to evolve alongside the codebase it describes.

> **On construction:** This document was built through human-AI collaboration — and is itself part of an experiment in whether AI can meaningfully architect its own runtime infrastructure. AI-assisted construction is not a shortcut — it is a new way of building. Because LLMs are part of the process, some inaccuracies may appear. We correct as we find them.

---

## 0. Where We Stand

The Portable Core thesis got the foundations right. The KEK/DEK separation is sound. The vault export with envelope encryption works. The four encryption surfaces — database-at-rest, field-level, vault export, inference transit — are the correct boundaries. The philosophy of cryptographic mortality is the correct philosophy.

A full audit of the implementation revealed gaps between what the thesis promises and what the code enforces. Some are straightforward oversights — parameters left at defaults, infrastructure built but never activated. Others are architectural improvements the original thesis did not consider: per-domain key compartmentalization, cryptographic identity, integrity attestation, and a more honest treatment of the inference transit boundary.

This document addresses both categories. It is not a replacement for the Portable Core thesis. It is the second pass — the one where we go from "the right ideas" to "the right implementation."

---

## 1. The Improved Key Hierarchy

### 1.1 The Problem: Two Independent Key Paths

The current architecture has two separate key derivation paths that are not documented together and create an uneven security posture:

1. **SQLCipher path**: The `ANIMA_CORE_PASSPHRASE` environment variable is passed to SQLCipher, which runs it through its own internal PBKDF2-HMAC-SHA512 (256,000 iterations by default in SQLCipher 4.x) to derive the database encryption key.

2. **Field encryption path**: The user's login password is run through Argon2id (time_cost=3, memory_cost=64MiB, parallelism=1) to derive a KEK, which wraps a random DEK used for AES-256-GCM field-level encryption.

These two paths are independent. They use different passphrases, different KDFs, and produce unrelated keys. The SQLCipher path uses PBKDF2, which is not memory-hard and is significantly weaker against GPU/ASIC attacks than Argon2id. An attacker who targets the weaker path gets the database — table structure, unencrypted columns, everything except the field-encrypted fields.

### 1.2 The Unified Design

The improved design derives everything from a single passphrase through a single Argon2id invocation, then uses HKDF (RFC 5869, Krawczyk) to derive domain-specific keys:

```
User Passphrase
       |
   Argon2id (t=4, m=64MiB, p=4, salt=16B)
       |
   Master KEK (32 bytes, never stored)
       |
       +-- HKDF-SHA256(master_kek, info="sqlcipher") --> SQLCipher DB Key
       +-- AES-KW --> DEK_conversations
       +-- AES-KW --> DEK_memories
       +-- AES-KW --> DEK_emotions
       +-- AES-KW --> DEK_selfmodel
       +-- AES-KW --> DEK_identity
       |
       +-- Succession (optional):
           Succession Passphrase --> Argon2id --> Succession KEK
                --> wraps subset of domain DEKs per transfer scope
```

The critical change for SQLCipher: by deriving the database key ourselves and passing it as a raw hex key (`PRAGMA key = "x'...'"`) we bypass SQLCipher's internal KDF entirely. We set `PRAGMA kdf_iter = 1` because the key derivation has already happened — in Argon2id, where it belongs. This eliminates the weaker PBKDF2 path and ensures the entire Core is protected by the same memory-hard derivation.

HKDF is the right tool for this step. It is not a password-hashing function — it is a key derivation function designed to extract and expand already-strong keying material into multiple domain-specific keys. The Argon2id output is strong keying material. HKDF-SHA256 with distinct `info` parameters produces cryptographically independent keys from it. This is exactly the construction HKDF was designed for.

### 1.3 Updated Parameters

The Argon2id parameters are tuned upward from the current defaults:

| Parameter   | Current | Proposed | Rationale                                              |
| ----------- | ------- | -------- | ------------------------------------------------------ |
| time_cost   | 3       | 4        | Closer to OWASP recommended minimum                   |
| memory_cost | 64 MiB  | 64 MiB   | Already adequate for consumer hardware                 |
| parallelism | 1       | 4        | Utilize multiple cores; matches typical consumer CPUs  |
| key_length  | 32      | 32       | 256-bit master key                                     |
| salt_length | 16      | 16       | 128-bit salt, sufficient                               |

These parameters should produce roughly 1-2 seconds of wall-clock time on typical consumer hardware. The salt and parameters are stored alongside the wrapped DEKs so derivation is reproducible on any machine.

---

## 2. Per-Domain DEKs: Compartmentalized Encryption

### 2.1 Why One Key Is Not Enough

The current architecture uses a single DEK for all field-level encryption. If that key is compromised — through a memory dump, a side channel, a bug in key handling — every encrypted field in the database is exposed at once. Conversations, memories, emotions, the self-model, identity data: all of it.

Per-domain DEKs limit the blast radius. A compromise of `DEK_conversations` exposes conversation history but not the self-model. A compromise of `DEK_emotions` exposes emotional signals but not memories. Each domain is an independent cryptographic compartment.

### 2.2 The Domain Mapping

| Domain          | DEK                 | Tables Covered                                        |
| --------------- | ------------------- | ----------------------------------------------------- |
| conversations   | DEK_conversations   | agent_messages, thread metadata                       |
| memories        | DEK_memories        | memory_items, memory_episodes, daily_logs             |
| emotions        | DEK_emotions        | emotional_signals, emotional_synthesis                |
| self-model      | DEK_selfmodel       | self_model_blocks (identity, inner_state, growth_log) |
| identity        | DEK_identity        | Core keypair (Section 6), user directives             |

### 2.3 Succession Scopes Become Key Selection

This is where per-domain DEKs pay their largest dividend. The Succession Protocol defines three transfer scopes: `full`, `memories_only`, and `anonymized`. Today, implementing these scopes requires the system to selectively delete data during the claim transaction — a destructive, time-sensitive operation.

With per-domain DEKs, transfer scopes become key-selection problems:

- **Full**: The succession KEK wraps all five domain DEKs.
- **Memories only**: The succession KEK wraps `DEK_memories` and `DEK_selfmodel`. Conversations are not wrapped — and therefore not recoverable by the beneficiary.
- **Anonymized**: The succession KEK wraps only `DEK_selfmodel`. The AI's personality and learned patterns transfer. Raw memories and conversations do not.

No data deletion required. The data is still there, encrypted on disk, but the beneficiary simply does not have the keys to decrypt the domains outside their scope. The data dies with the original passphrase — cryptographic mortality applied selectively.

### 2.4 User Experience Is Unchanged

The user still enters one passphrase. The system derives the Master KEK, unwraps all domain DEKs, and holds them in the session store. From the user's perspective, nothing has changed. The compartmentalization is invisible until it matters — and when it matters, it matters a great deal.

### 2.5 Storage

The `user_keys` table gains a `domain` column:

```sql
ALTER TABLE user_keys ADD COLUMN domain VARCHAR(32) NOT NULL DEFAULT 'default';
```

During migration, the existing single wrapped DEK is duplicated into domain-specific entries. Each domain DEK is a fresh 32-byte random key, and the existing data is re-encrypted under the appropriate domain key. This is a one-time migration — slow, but atomic and verifiable.

---

## 3. AAD Enforcement: Binding Ciphertext to Context

### 3.1 The Gap

The Portable Core thesis states clearly:

> Every encrypted field must be bound to its context: which user it belongs to, which table, which record.

The code implements this. The `ef()` function accepts `table` and `field` parameters and constructs an AAD string. When provided, the ciphertext is bound to its context — moving it to a different table, a different field, or a different user produces a decryption failure.

The problem: every call site in the codebase omits these parameters. The AAD is always `None`. Every encrypted field uses the `enc1` prefix (no AAD) rather than `enc2` (with AAD). The infrastructure is dormant.

This means an attacker with write access to the database — or a bug in the application — could swap encrypted values between fields, between tables, or between users, and the system would decrypt them without complaint. A user's encrypted self-model could be replaced with their encrypted conversation history. An emotional signal from one user could be moved to another. The decryption would succeed because the ciphertext is not bound to its location.

### 3.2 The AAD Format

The AAD string should encode enough context to prevent cross-table, cross-field, and cross-user swapping:

```
"{table}:{user_id}:{field}"
```

For example: `"memory_items:42:content"` or `"self_model_blocks:42:block_text"`.

This is not a secret. AAD is authenticated but not encrypted — it is additional data that must match at decryption time. Its purpose is binding, not confidentiality.

### 3.3 Enforcement

AAD should not be optional. The `ef()` function should require `table` and `field` parameters, and the codebase should not contain a single call site that omits them. This is a linter-enforceable constraint — a static analysis rule that flags any `ef()` call without explicit AAD parameters.

### 3.4 Migration Path

Existing data is encrypted with the `enc1` prefix (no AAD). New data should use `enc2` (with AAD). The migration path:

1. Update `ef()` to require AAD parameters. All new encryptions produce `enc2` ciphertext.
2. The `df()` decryption function continues to accept both `enc1` and `enc2` — backward compatibility during the transition.
3. A background migration task re-encrypts `enc1` values as `enc2` with proper AAD. This can run during a deep reflection cycle or at unlock time.
4. Once migration is complete, `df()` can optionally reject `enc1` values — or continue accepting them as a safety net.

The migration is not urgent in the threat model — AAD prevents a specific class of manipulation that requires database write access. But leaving it dormant indefinitely undermines the architectural promise. The infrastructure was built for a reason.

---

## 4. Sealing the Boundary

### 4.1 The Promise vs. The Reality

The Portable Core thesis makes a clear commitment:

> All personal data must live inside the encrypted database. Every file that exists outside the sealed boundary is a hole in the portability promise.

The audit found that this boundary has leaks. The `users/{id}/` directory can contain plaintext files — markdown documents, legacy memory files, any artifact that predates the migration to database-backed storage. These files exist outside both SQLCipher and field-level encryption. If someone copies the `.anima/` directory, these files are readable without the passphrase.

### 4.2 What Must Be Sealed

Every byte of user-personal data must live inside the encrypted database. This includes:

- Memory items (already in DB)
- Conversation history (already in DB)
- Self-model blocks (already in DB, migrated from files)
- Emotional signals (already in DB)
- User directives (already in DB)
- Any remaining legacy files in `users/{id}/`

The migration from file-backed to database-backed storage is largely complete. What remains is ensuring that no new code paths create files outside the boundary, and that legacy files are migrated on first unlock rather than left in place.

### 4.3 Derived Data

Some data in the Core directory is derived — embedding caches, vector indices, the Chroma directory. These are rebuildable from canonical data in the database and should be:

1. Clearly marked as derived (e.g., in the manifest).
2. Excluded from vault exports.
3. Deletable without data loss.
4. Not containing any information that is not already in the encrypted database.

The principle: if you delete everything outside `anima.db` and `manifest.json`, the Core should be fully recoverable on next unlock. Everything else is cache.

### 4.4 The Audit

A periodic startup check should verify that no unexpected files exist in user directories. If found, they should be flagged — not silently deleted, but surfaced to the user as a boundary violation. "These files exist outside the encrypted boundary. Would you like to migrate them into the database or delete them?"

---

## 5. SQLCipher Configuration Hardening

### 5.1 Current State

The SQLCipher integration passes the passphrase via `PRAGMA key` and relies on all other settings being SQLCipher defaults. No explicit configuration for page size, KDF iterations, memory security, or cipher compatibility.

This is functional but fragile. SQLCipher defaults can change between versions. An upgrade could silently change the KDF iteration count or page size, making an existing database unopenable until the old parameters are explicitly set.

### 5.2 The Correct Configuration

With the unified key hierarchy from Section 1 — where we derive the SQLCipher key ourselves via HKDF and pass it as a raw hex key — the configuration becomes:

```sql
PRAGMA key = "x'<64-hex-chars>'";          -- raw 256-bit key, no internal KDF
PRAGMA cipher_page_size = 4096;            -- explicit, matches SQLite default
PRAGMA kdf_iter = 1;                       -- KDF already done by Argon2id
PRAGMA cipher_memory_security = ON;        -- zero memory on free (defense-in-depth)
PRAGMA cipher_compatibility = 4;           -- pin to SQLCipher 4 format
```

The key points:

- **`kdf_iter = 1`**: Because we derived the key externally with Argon2id, SQLCipher's internal PBKDF2 is redundant. Setting it to 1 eliminates the double-derivation overhead and removes the weaker KDF from the path entirely.
- **`cipher_page_size = 4096`**: Pinned explicitly so it survives SQLCipher upgrades.
- **`cipher_memory_security = ON`**: Instructs SQLCipher to zero memory allocations on free. This is defense-in-depth — it complements the session-level key zeroing we already do with `ctypes.memset`.
- **`cipher_compatibility = 4`**: Pins the cipher format. Without this, a future SQLCipher version could default to a new format and fail to open existing databases.

These PRAGMAs should be recorded in the manifest so that any host knows how to open the database without guessing.

---

## 6. Cryptographic Identity: The Core Keypair

### 6.1 Why the Core Needs a Key

The Portable Core thesis defines the Core as a self-describing, portable artifact. But it has no way to prove its own integrity, sign its own output, or establish identity beyond "whoever has the passphrase."

A cryptographic keypair changes this. At Core creation time, an Ed25519 keypair (Bernstein et al.) is generated:

- The **private key** is encrypted with `DEK_identity` and stored in the database.
- The **public key** is stored in `manifest.json` — readable without the passphrase.

The private key never leaves the encrypted boundary. The public key is the Core's identity — its fingerprint, verifiable by anyone.

### 6.2 What the Keypair Enables

```
                    Ed25519 Keypair
                    (generated at Core creation)
                           |
            +--------------+--------------+
            |              |              |
      Output Signing  Vault Signing  Core Attestation
            |              |              |
     "ANIMA said this"  "This vault    "This Core has
      (provenance for    is authentic"   not been tampered
       AI responses)    (tamper-detect   with since it was
                         without         last locked"
                         passphrase)
            |
            +-- DID:key
                (decentralized identity,
                 no blockchain required)
```

**Output signing.** Every AI response can be signed with the Core's private key. The signature proves that this specific ANIMA instance produced this specific output. This is provenance — not for legal purposes, but for the user to verify that a response came from their AI and was not injected or altered.

**Vault signing.** When exporting a vault, the envelope is signed with the Core's private key. Anyone with the public key (from the manifest) can verify that the vault was produced by this Core — without needing the passphrase. This detects tampering before you even attempt decryption.

**Core attestation.** Section 7 covers this in detail — the keypair enables tamper detection for the database itself.

**Decentralized identity.** The public key maps directly to a DID (Decentralized Identifier) using the `did:key` method. No blockchain. No registration service. The Core's identity is its public key, self-certifying and portable. This is future-facing — it positions the Core for verifiable credentials, cross-Core communication, and provenance chains without requiring any infrastructure beyond the keypair itself.

### 6.3 Key Lifecycle

The keypair is generated once, at Core creation, and never rotated. The private key is as permanent as the Core itself — it is part of the Core's identity. If the Core is destroyed, the keypair is destroyed with it. If the Core is transferred via succession, the keypair transfers with it — the new owner inherits the Core's identity along with its memories.

This is a deliberate choice. Rotating the identity key would break all previously issued signatures. The identity of the Core is fixed at birth, like a fingerprint. What changes is the relationship, the memories, the self-model. The identity key is the anchor.

---

## 7. Core Integrity Attestation

### 7.1 The Problem

When the Core is locked — the session has ended, the DEK has been zeroed from memory — the database file sits on disk, encrypted. If an adversary modifies the file while it is locked (flipping bits, truncating tables, replacing encrypted blobs), the current architecture has no way to detect this at unlock time. Decryption might fail for individual fields, but there is no holistic integrity check.

### 7.2 The Mechanism

At lock time (session end), the system:

1. Computes a SHA-256 hash of each critical table's contents (conversations, memories, self-model, emotional signals, user keys).
2. Assembles these hashes into a Merkle tree.
3. Signs the Merkle root with the Core's Ed25519 private key.
4. Stores the signed attestation outside the encrypted database — in the manifest or in a separate `.attestation` file in the Core directory.

At unlock time, after decryption:

1. Recomputes the Merkle tree from the decrypted table contents.
2. Verifies the signature on the stored attestation using the public key from the manifest.
3. Compares the recomputed root to the attested root.

### 7.3 What Happens on Mismatch

If the attestation fails — the Merkle root does not match — the system warns the user. It does not refuse to open the Core. The user decides.

This is a deliberate design principle. The Core belongs to the user. If they moved it, restored from backup, manually edited the database, or upgraded the schema — all of these could legitimately change the Merkle root. The attestation is a tamper-detection signal, not a lockout mechanism.

The warning should be clear and specific: "The Core's contents have changed since it was last locked. This could mean tampering, or it could mean you restored from a backup or modified the database. Proceed with caution."

### 7.4 What the Attestation Does Not Cover

The attestation protects against modification of the encrypted database while the Core is locked. It does not protect against:

- An adversary with the passphrase (they can unlock, modify, and re-attest).
- Modifications during an active session (the database is open and writable).
- Compromise of the Ed25519 private key (they can forge attestations).

The attestation is one layer. It complements SQLCipher's built-in HMAC page-level authentication, which detects corruption at a lower level. Together, they provide defense-in-depth: SQLCipher catches page-level corruption; the attestation catches table-level manipulation.

---

## 8. Closing the Inference Transit Seam

The Portable Core thesis names the inference boundary honestly:

> The context window is the one moment where memories are exposed in plaintext to an external system.

This section does not pretend to solve that problem completely. It describes two complementary approaches that reduce the exposure, and acknowledges that full sovereignty requires local inference.

### 8.1 TEE-Aware Inference

Trusted Execution Environments create hardware-enforced enclaves where code and data are protected from the host operating system. For inference, this means the model and the context window are shielded from the cloud provider — the provider's own administrators cannot read the data being processed.

The landscape is maturing:

- **Intel TDX** (Trust Domain Extensions): VM-level isolation with less than 10% overhead on supported hardware.
- **NVIDIA H100 Confidential Computing**: GPU TEE with 4-8% performance penalty. Enables confidential inference on large models.
- **Phala Network** already runs DeepSeek R1 70B in GPU TEEs accessible via OpenRouter.

For AnimaOS, this translates to a concrete, near-term improvement:

1. Add a `provider_tee_mode` configuration option (e.g., `"none"`, `"tee_preferred"`, `"tee_required"`).
2. When routing to OpenRouter or compatible providers, prefer endpoints that advertise TEE attestation.
3. Verify the attestation report before sending context (the provider proves its enclave is genuine).
4. Display a privacy indicator in the UI — a simple signal showing whether the current inference is TEE-protected.

**Honest caveat**: TEE.Fail (October 2025) demonstrated side-channel attacks against Intel TDX that could leak data across trust boundaries. The academic community is actively working on mitigations. For most users — whose adversary is the cloud provider's business practices, not a nation-state — TEE protection is a significant improvement over plaintext inference. For users whose threat model includes sophisticated side-channel attacks, local inference remains the answer.

### 8.2 Context Sanitization Pipeline

Before sending context to any remote model, apply data minimization:

1. **Pseudonymization**: Replace real names with consistent pseudonyms (e.g., "Alex" becomes "Person A" throughout the context). Consistency matters — the model needs to track referents across the conversation.
2. **Date generalization**: Replace specific dates with relative references ("last Tuesday" becomes "recently"). Temporal reasoning is usually sufficient at lower precision.
3. **Sensitivity filtering**: Memories below a configurable sensitivity threshold are sent as-is. Memories above the threshold are either summarized or omitted, with a note that sensitive context was withheld.
4. **Audit logging**: Every context sent to a remote model is logged (encrypted, in the database) for the user's review. What was sent, to which provider, when. The user can see exactly what left the boundary.

This is data minimization, not formal differential privacy. Differential privacy (Dwork, 2006) provides mathematical guarantees about information leakage — but those guarantees apply to aggregate queries over populations, not to individual conversational context. Calling this "differential privacy" would be misleading. It is practical sanitization: reduce the identifiable information in transit without destroying the context the model needs to reason.

### 8.3 Local Inference: The Destination

Local inference eliminates the transit boundary entirely. The model runs on the user's hardware. The context never leaves the machine. This is the end state the architecture is designed for.

The current reality: local models are not yet at the quality level required for ANIMA's full capabilities on most consumer hardware. But they are improving rapidly, and the architecture — soul local, mind remote — is designed so that switching to local inference requires no structural changes. The mind becomes local. The soul was always local.

---

## 9. Vault Hardening

The vault is the Core's canonical portable form. The current implementation works — encrypted envelope, Argon2id key derivation, integrity hash. But several improvements would make it more robust.

### 9.1 Compression

The vault currently encrypts the JSON payload directly without compression. For a Core with years of conversation history, this means the vault is significantly larger than necessary.

The recommendation: compress with zstd (Collet, 2015) before encryption.

In some contexts, compressing before encrypting is dangerous — compression oracles like CRIME and BREACH exploit the relationship between plaintext content and compressed size to leak secrets. But those attacks require an adversary who can inject chosen plaintext and observe the resulting ciphertext size. The vault is a one-shot export with no adversary-controlled input. There is no compression oracle vector here.

The order is: serialize, compress, encrypt, sign.

### 9.2 Ed25519 Signature

With the Core keypair from Section 6, the vault envelope gains a signature:

```json
{
  "version": 3,
  "kdf_params": { ... },
  "encrypted_payload": "...",
  "integrity_hash": "sha256:...",
  "signature": "ed25519:<base64>"
}
```

The signature covers the encrypted payload and the integrity hash. Anyone with the Core's public key can verify that the vault was produced by this Core — without the passphrase. This catches tampering before decryption is even attempted.

**Important distinction**: The SHA-256 `integrity_hash` is a corruption-detection optimization. It detects accidental bit-flips during storage or transfer — the kind of damage that a USB drive or cloud sync might introduce. It is not a security measure. An attacker who can modify the vault can also recompute the hash. The Ed25519 signature is the security measure — it proves the vault has not been modified since the Core signed it.

### 9.3 Rollback Detection

Without a monotonic counter, an adversary (or a sync conflict) could replace a current vault with an older one. The user imports what they think is their latest backup and loses months of accumulated memory.

The fix: a vault sequence number, stored in the manifest and incremented on each export. The import process checks the sequence number against the manifest and warns if the vault is older than the last known export.

This is not a hard rejection — the user might intentionally import an older vault. But they should know.

### 9.4 Vault Version Bump

These changes — compression, signing, rollback detection — constitute a vault version bump from v2 to v3. The import path must continue to accept v2 vaults (without compression or signature) indefinitely. The export path produces v3 by default.

---

## 10. Honest Limitations

Cryptographic architecture documents tend to present their designs as watertight. This section is the counterweight. These are the places where the architecture is best-effort, where guarantees are aspirational, or where the threat model has known gaps.

### 10.1 Key Zeroing in Python

The session store uses `ctypes.memset` to zero the DEK from memory on session end. This is defense-in-depth, not a guarantee.

Python's memory model does not support secure erasure. The `bytes` type is immutable — creating a DEK as a `bytes` object means the key material may exist in multiple locations: the original allocation, any copies the garbage collector made, the free list. `ctypes.memset` zeros the buffer at a known address, but it cannot chase down copies the runtime may have made.

A language with explicit memory control (Rust, C) can guarantee secure erasure. Python cannot. The mitigation is to minimize the DEK's lifetime in memory (24-hour session TTL), use `ctypes.memset` as a best-effort cleanup, and accept that a sophisticated adversary with a memory dump could potentially recover key material from a running Python process.

For the threat model that matters most — someone copies the `.anima/` directory — this limitation is irrelevant. The keys are not in the directory. They exist only in the memory of a running process, and only for the duration of a session.

### 10.2 DEK Residency

The 24-hour session TTL means the DEK exists in process memory for up to 24 hours. During that window, any OS-level compromise — a malicious process with debug privileges, a memory-dumping exploit, a cold boot attack — could recover the key.

This is the cost of usability. Re-deriving from the passphrase on every request would be secure but unusably slow (1-2 seconds of Argon2id per operation). The 24-hour window is a compromise between security and user experience.

A shorter TTL (e.g., 1 hour with re-prompt) would reduce the exposure window. This should be a user-configurable option for those with stricter threat models.

### 10.3 The Inference Seam

TEE-aware inference and context sanitization reduce the exposure of the inference transit boundary. They do not eliminate it. Even with TEE protection, the model provider's hardware processes the plaintext context. Even with sanitization, enough context must be sent for the model to be useful.

Full sovereignty requires local inference. Everything else is harm reduction. The architecture should be honest about this distinction.

### 10.4 Post-Quantum Readiness

AES-256 provides 128-bit security against quantum adversaries (Grover's algorithm halves the effective key length). This is sufficient. No action needed on the symmetric side.

If X25519 key exchange is added for vault forward secrecy (as the Portable Core thesis suggests), a post-quantum concern arises: Shor's algorithm would break X25519. The mitigation is hybrid construction — combine X25519 with ML-KEM (formerly CRYSTALS-Kyber, NIST FIPS 203). The combined shared secret is at least as strong as the stronger component. This is the approach TLS 1.3 deployments are already adopting.

For the current architecture — which uses only symmetric cryptography and Ed25519 signing — post-quantum risk is limited to signature forgery, not confidentiality. An attacker with a quantum computer could forge Ed25519 signatures but could not decrypt AES-256-GCM ciphertext. The priority is correct: protect confidentiality first (symmetric, already PQ-safe), upgrade signatures when PQ signature standards mature (SLH-DSA, NIST FIPS 205).

---

## 11. What We Chose Not to Build (and Why)

Cryptographic research offers many constructions that sound appealing in the abstract. Not all of them make sense for a local-first, single-user AI companion. This section documents what we evaluated and why we passed.

**Fully Homomorphic Encryption (FHE) for inference.** FHE (Gentry, 2009; lattice-based schemes like CKKS, BFV) would allow the model to reason over encrypted context without ever seeing plaintext. The overhead is 10,000x or more for non-trivial computations. A single inference call that takes 2 seconds in plaintext would take hours under FHE. This is years from viable for conversational AI. We track the field but do not design for it.

**Oblivious RAM (ORAM).** ORAM hides access patterns from an adversary who can observe memory accesses. The threat model is narrow — it matters when the adversary is the cloud host watching which database pages you read. For a local-first system where the database is on the user's own disk, ORAM provides no benefit. Full-disk encryption is the pragmatic answer.

**Proxy re-encryption.** PRE allows a proxy to transform ciphertext from one key to another without seeing the plaintext. Useful in multi-user, multi-server architectures. AnimaOS is single-user, local-first. The succession protocol's re-keying is simpler and sufficient.

**Puncturable encryption.** PE allows revoking the ability to decrypt specific ciphertexts — the key "forgets" how to decrypt them. Useful for forward secrecy in messaging. For a database at rest, SQLCipher's `PRAGMA secure_delete = ON` (which overwrites deleted pages with zeros) achieves the practical goal. No production-ready puncturable encryption libraries exist for our use case.

**Searchable encryption.** SE (Song, Wagner, Perrig) allows queries over encrypted data without decryption. The constructions support equality queries and range queries — not semantic similarity search. AnimaOS's retrieval is embedding-based cosine similarity, which is fundamentally incompatible with current SE schemes. The database is decrypted in memory during a session; search operates on plaintext in-process. SE adds complexity without enabling the search we actually need.

**Plausible deniability (hidden volumes).** TrueCrypt/VeraCrypt-style hidden volumes provide plausible deniability — under coercion, you reveal a decoy passphrase that opens a benign dataset. The construction is sound but adds significant complexity: the Core would need to maintain two parallel datasets, and every write pattern must be indistinguishable between the real and decoy volumes. This is a niche threat model (coercion by adversaries who can compel passphrase disclosure) that is documented here as a future possibility for users who need it, not as a near-term priority.

---

## 12. Summary

| Dimension                      | Current State                             | Hardened State                                          |
| ------------------------------ | ----------------------------------------- | ------------------------------------------------------- |
| **Key derivation**             | Two independent paths (PBKDF2 + Argon2id) | Single Argon2id + HKDF derivation tree                  |
| **Data encryption keys**       | Single DEK for all fields                 | Per-domain DEKs with compartmentalized blast radius     |
| **AAD binding**                | Infrastructure exists, never used         | Mandatory, linter-enforced, `enc2` prefix               |
| **Encryption boundary**        | Leaks possible via user directory files   | All personal data inside encrypted database             |
| **SQLCipher configuration**    | Defaults, no explicit PRAGMAs             | Pinned PRAGMAs, raw key injection, no internal KDF      |
| **Cryptographic identity**     | None                                      | Ed25519 keypair, output signing, DID:key                |
| **Integrity attestation**      | None                                      | Merkle root signed at lock, verified at unlock          |
| **Inference transit**          | Plaintext to remote model                 | TEE-aware routing + context sanitization pipeline       |
| **Vault compression**          | None                                      | zstd before encryption                                  |
| **Vault signing**              | SHA-256 integrity hash only               | Ed25519 signature (security) + SHA-256 (corruption)     |
| **Vault rollback detection**   | None                                      | Monotonic sequence number in manifest                   |
| **Succession key management**  | Wraps single DEK                          | Wraps subset of domain DEKs per transfer scope          |
| **Post-quantum readiness**     | AES-256 (sufficient)                      | Hybrid ML-KEM if X25519 is added for forward secrecy   |

The Portable Core thesis gave us the right philosophy. This document gives us the right parameters.

The passphrase is still the root. The Core is still mortal. The memories are still yours. What changes is the rigor: every gap sealed, every parameter pinned, every promise enforced. The architecture earns the claims the thesis makes.
