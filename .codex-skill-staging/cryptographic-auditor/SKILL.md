---
name: cryptographic-auditor
description: Review code, configs, prompts, and design claims for cryptographic implementation flaws, broken key-management lifecycles, unsafe encryption usage, vault/export integrity gaps, and security guarantees that are claimed but not enforced. Use when the user asks for a crypto audit, key-management review, passphrase/KDF/AEAD review, vault/export/import hardening, secure deletion validation, or thesis/design-to-code guarantee-gap analysis. Do not use for broad AppSec review; use security-auditor for general security audits and security-threat-model for repo-level threat modeling.
---

# Cryptographic Auditor

Perform an implementation-grounded cryptography review. Prioritize broken guarantees and misuse of sound primitives over generic crypto commentary.

## Quick Start

1. Define the scope: repo, path, diff, workflow, export/import path, or documented guarantee.
2. State the claimed property: confidentiality, integrity, authenticity, compartmentalization, or deletion.
3. Trace the key lifecycle: generation, derivation, wrapping, storage, loading, caching, rotation, and destruction.
4. Verify nonce handling, AAD/context binding, tamper detection, and plaintext exposure paths.
5. Report findings first, ordered by severity, with evidence and the exact guarantee that fails.

## Repo Map

Start with these files in AnimaOS:

- `apps/server/src/anima_server/services/crypto.py`
- `apps/server/src/anima_server/services/data_crypto.py`
- `apps/server/src/anima_server/services/vault.py`
- `apps/server/src/anima_server/services/sessions.py`
- `apps/server/src/anima_server/services/auth.py`
- `apps/server/src/anima_server/db/session.py`
- `apps/server/src/anima_server/models/user_key.py`
- `docs/thesis/whitepaper.md`
- `docs/thesis/cryptographic-hardening.md`
- `docs/thesis/succession-protocol.md`

Read the code before judging the docs. Distinguish what is implemented from what is merely promised.

## Review Workflow

### 1. Establish the claim and adversary

- Identify the protected asset and trust boundary.
- Name the attacker and their required access.
- Note whether the guarantee comes from code, comments, product behavior, or thesis/docs.

### 2. Trace the construction end to end

- Follow the path from route, setup flow, session bootstrap, or vault operation into the crypto helpers.
- Check primitive choice and parameters.
- Check key separation, serialization format, storage location, verification, and recovery/migration behavior.
- Confirm how failures surface and whether plaintext leaks in logs, errors, or temporary state.

### 3. Hunt for high-signal failure modes

- Weak or implicit KDF/cipher parameters
- Missing domain separation or key reuse across purposes
- Nonce or IV reuse, unsafe randomness, or deterministic leakage
- Missing or inert AAD/context binding
- Integrity checks that do not bind the right metadata
- Vault import/export tampering or downgrade paths
- Session caches or zeroization claims that are weaker than advertised
- "Cryptographic deletion" claims that are really application-level hiding
- Docs or whitepaper claims that the code cannot currently prove

### 4. Validate before reporting

- Prefer confirmed guarantee failures over speculative hardening ideas.
- Label each issue as `implemented but broken`, `partially implemented`, `documented only`, or `cannot verify`.
- Distinguish confidentiality failures from integrity failures.
- Check for mitigating controls before assigning severity.

## Output Contract

- Findings first, ordered by severity.
- For each finding, include: title, severity, claimed guarantee, attacker or failure mode, impact, evidence, and fix.
- Name a regression test or assertion that should prevent recurrence.
- After findings, list open questions or assumptions that affect confidence.
- If no findings are confirmed, state that explicitly and call out residual risk or testing gaps.

## Boundaries

- Use `security-auditor` for broader auth, authz, injection, or general attack-surface review.
- Use `security-threat-model` for architecture-wide abuse-path modeling.
- For pull requests or diffs, focus on crypto regressions introduced by the changed lines.
- Do not drift into generic cryptography advice that is not grounded in this codebase.
