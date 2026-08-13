# Portable Core Filesystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make encrypted Core objects the canonical home for portable user-owned content, restrict SQLCipher to ANIMA's internal Soul, and keep PostgreSQL outside `.anima/` as a disposable runtime/indexing service.

**Architecture:** animaOS remains the product; ANIMA CORE is its portable encrypted Soul-plus-CoreFS subsystem, while Runtime stays outside it. Rust owns reusable bounded file operations plus CoreFS encryption/catalog/atomic mutation; Python owns product domains, APIs, migrations, and runtime indexing through the existing `anima-core` native extension. Animus reuses the same file-operation contracts with an explicit HostFS backend. Build the Filesystem Root Key/per-object encryption and immutable catalog foundation first, then add progressive runtime indexing behind compatibility gates. Migrate diary/notes, conversations, assets/documents, and account/tasks/preferences as vertical slices. App-owned Soul tables remain read-only rollback material through the first cutover release; a separately approved later release performs physical cleanup after a stable observation window.

**Tech Stack:** Rust 2021 (`anima-file-tools`, `anima-corefs`, PyO3 via `anima-core`), Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, SQLCipher, embedded PostgreSQL/pgvector, Argon2id, AES-256-GCM, HKDF-SHA256, React/Vite/Tauri, TypeScript, pytest, Cargo, Bun/Nx.

---

## Planning Inputs

- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Storage design: `docs/superpowers/specs/2026-07-12-portable-core-filesystem-design.md`
- Security design: `docs/superpowers/specs/2026-07-12-portable-core-key-hierarchy-design.md`
- Target architecture diagrams: `docs/architecture/system/anima-core-filesystem.md`
- Existing architecture: `docs/thesis/three-tier-architecture.md`, `docs/architecture/README.md`
- Soul session/bootstrap: `apps/server/src/anima_server/db/session.py`, `db/user_store.py`
- Manifest/key code: `apps/server/src/anima_server/services/core.py`, `crypto.py`, `recovery.py`, `sessions.py`
- Runtime lifecycle: `apps/server/src/anima_server/db/runtime.py`, `db/pg_lifecycle.py`, `main.py`
- Existing content paths: `services/diary.py`, `services/agent/thread_manager.py`, `services/agent/transcript_archive.py`, `services/images/`, `api/routes/diary.py`, `threads.py`, `images.py`, `documents.py`, `tasks.py`, `config.py`, `auth.py`
- Existing Rust surfaces: `packages/anima-core`, `apps/animus/src/tools/files.rs`, `apps/animus/src/tools/mod.rs`, `apps/animus/src/permissions.rs`
- Production reference: the user-supplied sibling Apache-2.0 Codex checkout, audited at commit `9e552e9d15ba52bed7077d5357f3e18e330f8f38` (2026-07-11), especially `codex-rs/file-system`, `codex-rs/apply-patch`, protocol permissions, bounded output, and tool registry layers

## Execution Preconditions

- Do not execute this plan in the current dirty checkout.
- Create an isolated worktree from the approved dependency head using `superpowers:using-git-worktrees`.
- Suggested branch: `codex/portable-core-filesystem`.
- Open and claim the matching `PCF-*` ticket before each task; update the parent tracker with every status change.
- Keep compatibility reads until the vertical slice's parity, transfer, and rollback tests pass.
- Tasks 2-7 build converters and validate an inactive shadow catalog only. App routes and agent mutators remain on legacy authority (or return `corefs_migration_write_frozen`) until PCF-008 globally accepts the migration and publishes the authenticated first-write marker. No slice may publish an unmarked authoritative `fs/HEAD` mutation.
- Never stage unrelated files from the existing dirty worktree.

## Locked Decisions

1. Public name: **Portable Core Filesystem** / **Core Filesystem**. `CoreFS` is the implementation/API. V1 is virtual and unmounted; a future authenticated mount adapter is expected.
2. V1 catalog: full immutable snapshots, 25,000 live-entry / 16-MiB support envelope.
3. Canonical messages: versioned event records in 256-event / 1-MiB segments.
4. CoreFS plaintext search/embeddings: process memory only while unlocked.
5. Persistent PostgreSQL search assistance: opaque catalog/checkpoints and keyed blind tokens only.
6. Rollback: allowed until the first marked CoreFS mutation publishes `fs/HEAD`; forward-only afterward.
7. Runtime path: platform app-data root keyed by `core_id`, never `.anima/runtime`.
8. Soul cleanup: separate later release, gated by the authenticated forward-only marker, stable observation window, verified backup, and explicit approval.
9. Product naming: **animaOS** is the product; **ANIMA CORE** is the portable subsystem/export family; **CoreFS** remains the filesystem service/API.
10. Local transfer only: `anima_core_v2` streams `full`, `soul`, or `fs` artifacts to local disks/removable media; cloud upload/sync/backup is out of scope.
11. Default filenames: `anima-core-<timestamp>.anima`, `anima-core-soul-<timestamp>.anima`, and `anima-core-fs-<timestamp>.anima`.
12. Recovery separation: Soul-only restores to degraded `filesystem_missing`; CoreFS-only restores to restricted authenticated recovery/export mode; neither partial artifact impersonates a complete ANIMA, and CoreFS-to-Soul reattachment is deferred beyond V1.
13. Removable media: use one `.anima` file when supported and authenticated <=2-GiB multipart volumes when the destination's single-file limit requires them.
14. CoreFS folders are first-class stable catalog entries. Rename/move changes display placement, not stable ID or role; empty folders persist.
15. Folder policy separates `owner: user|anima|shared` from ANIMA access `none|read|write|manage`; policy inherits, explicit deny wins, and ANIMA cannot self-elevate user-owned content.
16. App roots use unique stable roles (`core.*` reserved; `client:<client-id>:*` namespaced), never hardcoded paths. Approved clients receive user-approved folder-scoped capabilities and cannot change their own grants.
17. Normal deletion is recoverable trash. Permanent purge and cryptographic deletion remain explicitly user-authorized `PCF-010` operations.
18. `packages/anima-file-tools` provides storage-agnostic bounded walk/glob/grep/read and typed patch planning. Animus HostFS and CoreFS are explicit backends with distinct tool names; path strings never select authority.
19. `packages/anima-corefs` owns encryption, catalog, folders/policy, streams, revisions, atomic mutations, trash, and restore. Python uses it through the existing `anima-core` PyO3 extension.
20. Selectively adapted Codex source retains Apache-2.0 headers and provenance in `THIRD_PARTY_NOTICES.md`; `anima-file-tools` is declared Apache-2.0 when it contains adapted Codex code rather than inheriting the workspace's MIT-only package metadata. animaOS never depends on the local Codex checkout at runtime.
21. CoreFS multi-file patches preflight every operation and publish one catalog generation or none, even if HostFS exposes only best-effort multi-file semantics.
22. Client-authored folders, namespaced roles, and metadata are portable; executable grants are authenticated device-local records bound to installed package identity/Core/instance/folder/scope and require reapproval after transfer.

## Target File Structure

New focused Rust crates and bindings:

```text
packages/anima-file-tools/
  Cargo.toml             # explicit Apache-2.0 metadata when Codex code is adapted
  src/lib.rs             # small public contract
  src/backend.rs         # explicit BackendKind and capability traits
  src/limits.rs          # chunk/walk/result ceilings
  src/walk.rs            # bounded lazy walk and pagination
  src/search.rs          # glob/grep contracts and result shaping
  src/read.rs            # bounded streaming/head-tail reads
  src/patch/             # parser, typed plan, validation, diagnostics
  tests/                 # backend conformance + adapted parser scenarios

packages/anima-corefs/
  Cargo.toml
  src/lib.rs             # small public CoreFS API
  src/crypto.rs          # FRK subkeys, DEKs, wrapping
  src/envelope.rs        # bounded .acore streams
  src/catalog/           # immutable generations and fs/HEAD
  src/folders.rs         # stable IDs, roles, hierarchy, metadata
  src/policy.rs          # owner/access/client capability evaluation
  src/backend.rs         # anima-file-tools CoreFS backend
  src/transaction.rs     # preflight + one-generation atomic commit
  src/trash.rs           # recoverable delete/restore
  tests/                 # crypto/catalog/policy/failure-injection suites

packages/anima-core/
  src/ffi.rs             # PyO3 wrapper/re-export for anima-corefs

apps/animus/src/tools/files/
  backend.rs             # HostFS adapter + existing containment policy
  handlers.rs            # explicit host tool handlers

third_party/
  licenses/Apache-2.0.txt
  notices/openai-codex-NOTICE.txt

scripts/check_codex_attribution.py
```

New focused server package:

```text
apps/server/src/anima_server/services/corefs/
  __init__.py          # exported service boundary
  types.py             # object/catalog/message/readiness value objects
  keyslots.py          # manifest + Soul credential generations
  crypto.py            # Python orchestration over Rust crypto/blind-token bindings
  filesystem.py         # domain/API facade over the Rust CoreFS engine
  clients.py            # client registration, namespaced roles, approved grants
  formats.py           # Markdown/HTML/JSON/JSONL validation and codecs
  messages.py          # conversation event/segment/projection rules
  indexer.py           # catalog reconcile, readiness, memory search, blind tokens
  migration.py         # resumable source converters and cutover states
  transfer.py          # local full/Soul/FS snapshot, streaming, multipart, import, and validation
```

New API/runtime surfaces:

```text
apps/server/src/anima_server/api/routes/corefs.py
apps/server/src/anima_server/schemas/corefs.py
apps/server/src/anima_server/models/corefs_runtime.py
apps/server/scripts/benchmark_corefs_catalog.py
apps/desktop/src/context/CoreFSReadinessContext.tsx
```

Keep domain parsing/response mapping in existing domain services. They call `CoreFS`; they do not reimplement encryption, catalogs, or physical paths.

## Task 1: Filesystem key hierarchy and credential generations

**Ticket:** `PCF-001`

**Files:**
- Create: `packages/anima-corefs/Cargo.toml`
- Create: `packages/anima-corefs/src/lib.rs`
- Create: `packages/anima-corefs/src/crypto.rs`
- Modify: `Cargo.toml`
- Modify: `packages/anima-core/Cargo.toml`
- Modify: `packages/anima-core/src/ffi.rs`
- Create: `apps/server/src/anima_server/services/corefs/__init__.py`
- Create: `apps/server/src/anima_server/services/corefs/types.py`
- Create: `apps/server/src/anima_server/services/corefs/keyslots.py`
- Create: `apps/server/src/anima_server/services/corefs/crypto.py`
- Create: `apps/server/src/anima_server/models/soul_keyslot.py`
- Create: `apps/server/alembic_core/versions/20260712_0001_add_soul_keyslots.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Modify: `apps/server/src/anima_server/services/core.py`
- Modify: `apps/server/src/anima_server/services/crypto.py`
- Modify: `apps/server/src/anima_server/services/recovery.py`
- Modify: `apps/server/src/anima_server/services/sessions.py`
- Modify: `apps/server/src/anima_server/db/user_store.py`
- Modify: `apps/server/src/anima_server/api/routes/auth.py`
- Modify: `packages/api-client/src/client.ts`
- Modify: `packages/api-client/src/types.ts`
- Modify: `apps/desktop/src/pages/settings/SecuritySettings.tsx`
- Test: `apps/server/tests/test_corefs_keyslots.py`
- Test: `apps/server/tests/test_corefs_crypto.py`
- Test: `apps/server/tests/test_recovery.py`
- Test: `apps/desktop/tests/recovery-credential-replacement.test.ts`

- [ ] **Step 1: Add failing key-hierarchy contract tests**

Cover stable opaque owner UUID provisioning, full password/recovery unlock of SQLCipher key + FRK, scoped `soul` completeness without FRK, scoped `fs` completeness without SQLCipher/Soul domains, complete legacy `user_keys` domain backfill, HKDF domain separation, per-object DEKs, exact object/chunk AAD fields, wrong-kind/keyslot rejection, no raw keys in serialized manifest, Rust/Python byte-for-byte vector parity, and zeroization/drop paths for native key material.

- [ ] **Step 2: Run focused tests and record the expected failures**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_crypto.py apps/server/tests/test_corefs_keyslots.py -q
```

Expected: Cargo/Python failures for missing `anima-corefs` and `corefs` bindings/modules.

- [ ] **Step 3: Implement value objects and versioned keyslot records**

Before creating any AAD-bound slot, atomically provision a stable random opaque owner UUID in a legacy manifest that has only numeric `owner_user_id`; record and verify the one-to-one numeric-to-opaque mapping without exposing username/profile fields. Add explicit enums/dataclasses for keyslot purpose, wrapping path, active/pending/decrypt-only status, credential generation, FRK version, and object-key epoch. Reject unknown algorithms and unsupported future versions rather than coercing them.

- [ ] **Step 4: Implement FRK subkey and per-object helpers**

Implement the canonical crypto helpers in `anima-corefs` using the repository's vetted Argon2id/AES-GCM/HKDF crates. Expose only typed opaque results through `anima-core` PyO3; Python must not duplicate envelope/key-wrapping cryptography. Required native interfaces:

```rust
pub fn derive_corefs_subkeys(frk: &SecretBytes, version: u32) -> Result<CoreFsSubkeys>;
pub fn create_object_dek(rng: &mut (impl RngCore + CryptoRng)) -> SecretBytes;
pub fn wrap_object_dek(dek: &SecretBytes, keys: &CoreFsSubkeys, aad: &ObjectKeyAad) -> Result<Vec<u8>>;
```

- [ ] **Step 5: Extend manifest keyslots without removing legacy fields**

Teach `services/core.py` to atomically read/write versioned password/recovery slots for SQLCipher and FRK while continuing to read existing `wrapped_sqlcipher_key` fields during compatibility migration.

- [ ] **Step 6: Add the Soul-internal `soul_keyslots` schema**

Create SQLCipher rows keyed by opaque owner ID, Soul domain, wrapping path, key version, credential generation, and status. Enumerate every legacy password- and recovery-wrapped `user_keys` domain for the sole owner, copy it into `soul_keyslots`, independently unwrap/compare the same domain key through both old and new records, and fail on missing, duplicate, unknown, or ambiguous domains. Preserve verified `user_keys` as rollback material until Task 9 cleanup.

- [ ] **Step 7: Implement cross-store password credential generation**

Write pending SQLCipher `soul_keyslots` and manifest root slots, reload/verify all of them with the new passphrase, activate manifest generation, then promote matching Soul rows. Replace the existing `/auth/change-password` direct `user_keys` update/standalone SQLCipher rewrap with this one coordinator as soon as new slots are provisioned; no live endpoint may bypass FRK/Soul generation consistency. Add failure injection between every durable boundary.

- [ ] **Step 8: Implement recovery credential replacement and initial FRK provisioning**

Require the recovery phrase and verify complete manifest + Soul-domain recovery credential generations for `full` mode. Define scoped generation records and state-preserving credential replacement for intentional `soul` and `fs` recovery: operate only on the authenticated declared compartment, reject undeclared cross-compartment slots, and never promote a scoped generation to `full`. Add the authenticated recovery-credential replacement endpoint to the existing auth router and a Security-settings flow that displays the new phrase once, requires explicit confirmation, and never logs/persists plaintext phrase material. Provision FRK v1 password/recovery keyslots and rotation-state types, but do not activate FRK rotation yet: catalog/`fs/HEAD` activation is implemented in Task 2 and blind-token rotation is completed in Task 3.

- [ ] **Step 9: Run focused crypto/recovery tests**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; cargo test -p anima-corefs -p anima-core
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_crypto.py apps/server/tests/test_corefs_keyslots.py apps/server/tests/test_crypto.py apps/server/tests/test_recovery.py apps/server/tests/test_encrypted_core_regression.py -q
bun test apps/desktop/tests/recovery-credential-replacement.test.ts
```

Expected: PASS.

- [ ] **Step 10: Commit the isolated foundation**

```powershell
git add Cargo.toml Cargo.lock packages/anima-corefs packages/anima-core/Cargo.toml packages/anima-core/src/ffi.rs apps/server/src/anima_server/services/corefs apps/server/src/anima_server/models/soul_keyslot.py apps/server/src/anima_server/models/__init__.py apps/server/src/anima_server/services/core.py apps/server/src/anima_server/services/crypto.py apps/server/src/anima_server/services/recovery.py apps/server/src/anima_server/services/sessions.py apps/server/src/anima_server/db/user_store.py apps/server/src/anima_server/api/routes/auth.py packages/api-client/src/client.ts packages/api-client/src/types.ts apps/desktop/src/pages/settings/SecuritySettings.tsx apps/server/alembic_core/versions/20260712_0001_add_soul_keyslots.py apps/server/tests/test_corefs_keyslots.py apps/server/tests/test_corefs_crypto.py apps/server/tests/test_recovery.py apps/desktop/tests/recovery-credential-replacement.test.ts
git -c commit.gpgsign=false commit -m "core: add portable filesystem key hierarchy"
```

## Task 2: Shared file tools, immutable object store, catalog, and CoreFS contract

**Ticket:** `PCF-002`  
**Depends on:** `PCF-001`

**Completed:** 2026-07-28.

Windows uses the accepted native object-validation lease; macOS and other unsupported
platforms retain the fail-closed safe-open validator. PR #125's reopened fallback-CI
repair passed current-head native CI and Codex review, and the synchronized second-phase
closeout is recorded. PCF-003 is dependency-eligible without being claimed.

**Files:**
- Create: `packages/anima-file-tools/Cargo.toml`
- Create: `packages/anima-file-tools/src/lib.rs`
- Create: `packages/anima-file-tools/src/backend.rs`
- Create: `packages/anima-file-tools/src/limits.rs`
- Create: `packages/anima-file-tools/src/walk.rs`
- Create: `packages/anima-file-tools/src/search.rs`
- Create: `packages/anima-file-tools/src/read.rs`
- Create: `packages/anima-file-tools/src/patch/`
- Create: `packages/anima-file-tools/tests/`
- Create: `packages/anima-corefs/src/envelope.rs`
- Create: `packages/anima-corefs/src/catalog/`
- Create: `packages/anima-corefs/src/folders.rs`
- Create: `packages/anima-corefs/src/policy.rs`
- Create: `packages/anima-corefs/src/backend.rs`
- Create: `packages/anima-corefs/src/transaction.rs`
- Create: `packages/anima-corefs/src/trash.rs`
- Create: `packages/anima-corefs/tests/`
- Create: `THIRD_PARTY_NOTICES.md`
- Create: `third_party/licenses/Apache-2.0.txt`
- Create: `third_party/notices/openai-codex-NOTICE.txt`
- Create: `scripts/check_codex_attribution.py`
- Create: `scripts/check_corefs_release_notices.py`
- Create: `.github/workflows/corefs-provenance.yml`
- Modify: `scripts/prepare-desktop-release.ts`
- Modify: `apps/desktop/src-tauri/tauri.conf.json`
- Modify: `Cargo.toml`
- Modify: `packages/anima-core/Cargo.toml`
- Modify: `packages/anima-core/src/ffi.rs`
- Modify: `apps/animus/Cargo.toml`
- Modify: `apps/animus/src/tools/files.rs`
- Create: `apps/animus/src/tools/files/backend.rs`
- Create: `apps/animus/src/tools/files/handlers.rs`
- Modify: `apps/animus/src/tools/mod.rs`
- Create: `apps/server/src/anima_server/services/corefs/formats.py`
- Create: `apps/server/src/anima_server/services/corefs/filesystem.py`
- Create: `apps/server/src/anima_server/services/corefs/clients.py`
- Create: `apps/server/src/anima_server/schemas/corefs.py`
- Create: `apps/server/src/anima_server/api/routes/corefs.py`
- Create: `apps/server/scripts/benchmark_corefs_catalog.py`
- Create: `docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json`
- Modify: `apps/server/src/anima_server/services/core.py`
- Modify: `apps/server/src/anima_server/main.py`
- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Test: `apps/server/tests/test_corefs_envelope.py`
- Test: `apps/server/tests/test_corefs_catalog.py`
- Test: `apps/server/tests/test_corefs_filesystem.py`
- Test: `apps/server/tests/test_corefs_tools.py`
- Test: `apps/server/tests/test_corefs_rotation.py`
- Test: `apps/server/tests/test_corefs_process_lock.py`
- Test: `apps/server/tests/test_corefs_clients.py`

- [x] **Step 1: Write failing envelope/catalog tests**

Cover raw-byte privacy, tamper/wrong-AAD rejection, immutable revisions, catalog-only logical paths, `fs/HEAD` validation, orphan non-resurrection, move/trash/restore/tombstone semantics, retained-catalog reads, portable NFC/case-sensitive path lookup, and first-class empty folders with stable IDs/roles across rename and move.

- [x] **Step 2: Write failing CoreFS safety tests**

Cover absolute paths, `..`, NUL, Unicode normalization collisions, reserved names, symlink/junction escape, output limits, optimistic revisions, explicit full `write(...)`, HTML sanitization boundaries, the per-principal operation matrix, owner/access inheritance, explicit-deny precedence, user-owned non-escalation, client-descendant policy inheritance, client-ID spoofing, manifest/payload substitution, unsigned digest mismatch, signature/publisher mismatch, package-ID collision, stale capability replay, update without reapproval, destination-machine reapproval, reserved-role collisions, lock-time handle revocation, and a real OS-backed interprocess Core/catalog lock. Spawn competing processes to prove simultaneous open/commit exclusion, safe crash recovery, and PID-reuse resistance using process-start identity rather than PID alone.

- [x] **Step 3: Extract the shared production file-tool library**

Create `anima-file-tools` with an explicit backend/capability trait, typed errors, stable pagination, cancellation/deadlines, bounded stream reads, lazy directory walking, glob/grep result shaping, and a typed apply-patch parser/planner. Set V1 defaults to 1-MiB read chunks, depth 64, 10,000 visited directories, 50,000 entries, and 4 MiB per model-visible response. Grep streams declared text, supports literal plus Rust linear-time regex modes, reports stable line/byte offsets, and bounds files/matches/line bytes before accumulation; binary/invalid-text behavior is explicit. Backend capabilities declare path/case semantics and mutation atomicity. Keep tool spec, handler, backend/runtime, and result shaping separate.

Selectively adapt the good Apache-2.0 Codex patterns audited at commit `9e552e9d15ba52bed7077d5357f3e18e330f8f38`: `codex-rs/file-system`, `codex-rs/apply-patch`, permission deny precedence, bounded head/tail output, and parser scenario tests. Do not copy host `PathUri`/sandbox assumptions or CoreFS-incompatible partial-success behavior. Declare `anima-file-tools` as Apache-2.0 if it incorporates adapted implementation and retain SPDX/Apache headers. Copy the full Apache-2.0 license and applicable upstream Codex NOTICE into `third_party/`; record every adapted upstream path, local destination, pinned commit, and modification summary in `THIRD_PARTY_NOTICES.md`.

Add `scripts/check_codex_attribution.py` to fail if an Apache-marked adapted file is absent from the inventory, required license/NOTICE text is missing from source, any Cargo/path dependency escapes the repository, or a source/build/test file references the sibling Codex checkout. Update `scripts/prepare-desktop-release.ts` and the Tauri resource map to stage `THIRD_PARTY_NOTICES.md`, the Apache-2.0 license, and the Codex NOTICE under the packaged desktop legal resources. Add `scripts/check_corefs_release_notices.py` to verify their exact hashes in the staged release tree and verify that the Tauri resource map includes that tree; any future standalone Animus distribution containing adapted code must pass the same artifact check.

Create `.github/workflows/corefs-provenance.yml` for pull requests touching the shared file/CoreFS crates or provenance files. Its checkout must contain only the animaOS repository, explicitly assert that no sibling Codex directory is present, and run the attribution checker, release-notice staging/checker, `cargo metadata --locked`, and the relevant build/tests. This workflow is the executable clean-standalone-checkout gate; a successful developer checkout with a sibling Codex directory is not release evidence.

- [x] **Step 4: Move Animus host-file mechanics onto the shared contracts**

Implement an explicit HostFS backend around Animus's existing workspace containment and Allow/Ask/Deny policy. Preserve host-facing tool names and behavior compatibility while replacing full-file `read_to_string`, eager recursive `Vec` accumulation, literal-only search internals, and ad hoc patching with bounded shared machinery. Host multi-file mutation reports its actual atomicity/capabilities; it must not claim CoreFS transaction semantics.

- [x] **Step 5: Implement Rust `.acore` envelope, catalog, and format boundary**

Implement streaming envelopes/catalogs in `anima-corefs`, expose them through `anima-core` PyO3, and keep physical names opaque and payload schemas versioned. Do not write decrypted temporary files. Python retains domain format validation/projection, not encryption.

- [x] **Step 6: Implement folders, policy, full immutable catalogs, and `fs/HEAD`**

`fs/HEAD` carries generation, catalog hash, envelope version, and required FRK version. Catalog entries carry stable object/folder ID, parent, mutable name, optional unique role, owner, ANIMA access, policy overrides, namespaced client metadata, object revision/hash/kind, wrapped Object DEK, trash/deletion state, and optional cutover marker. Every directory is first-class even when empty. Enforce reserved `core.*` and client `client:<client-id>:*` role namespaces.

- [x] **Step 7: Implement the Core-wide commit coordinator**

Prepare immutable object revisions outside the shared lock; acquire an OS-backed exclusive file lock (or atomic-create primitive with equivalent kernel exclusion) plus owner PID/process-start metadata; reload `fs/HEAD`; revalidate expected path/revision; publish catalog then `fs/HEAD`; emit invalidation after commit. Stale recovery must first prove the recorded process identity is gone; a check-then-write JSON file is not a lock.

- [x] **Step 8: Add failure injection around every publish boundary**

Prove crashes leave the prior `fs/HEAD` authoritative or the complete next generation committed, never a partially visible mutation.

- [x] **Step 9: Implement catalog-bound key rotation**

Add targeted object-key rotation, FRK catalog rewrap, pending-FRK `fs/HEAD` recovery, old catalog decryptability, and explicit old-key retirement gates. Leave blind-token generation switching incomplete until Task 3.

- [x] **Step 10: Implement the complete CoreFS logical contract and bounded agent tools**

Implement `list`, `walk`, `glob`, authoritative streaming `grep`, Runtime-index-backed `search`, `read`, `stat`, `mkdir`, `create`, full `write`, multi-file `apply_patch`, `move`, `trash`, and `restore`. Expose equivalently prefixed `corefs_*` wrappers. `search` reports index generation/readiness; `grep` remains available as a bounded canonical scan when indexes are incomplete. CoreFS patch execution must parse and preflight every path, policy, revision, collision, and format before writing, then publish one catalog generation or none. Return explicit `atomic: true`; never inherit Codex/HostFS partial-success semantics. Before PCF-008, reads target only the explicitly selected validation snapshot and every mutator returns `corefs_migration_write_frozen`; converter-only shadow writes are not published as authoritative `fs/HEAD`. Agent tools receive logical paths/stable IDs/results only, never raw keys or object-store paths. Host and CoreFS tools reject each other's URI/path forms and never auto-route.

- [x] **Step 11: Add client extensions and the generic authenticated API route**

Register `corefs_router` in `main.py`; require an unlocked Core session, authenticate the caller, and resolve the distinct user, ANIMA, or installed-client principal before evaluating that principal's folder capability. Require owner scope only for user-only operations such as policy, ownership, grants, reserved-role binding, purge, and key retirement. Enforce the same migration-write gate server-side regardless of caller and keep arbitrary host filesystem access impossible. The device-local broker, never the caller, canonicalizes the installed manifest, hashes package payloads, verifies optional publisher signatures against a user-trusted key, and assigns the local installation principal. Unsigned packages bind to an exact digest; V1 requires reapproval after every digest change even when signed. Reject conflicting publisher/digest claims on one package ID for explicit user resolution. Add explicit user-approved folder-scoped read/write/manage grants bound to Core/instance/install principal/folder/scope/generation, and issue only short-lived audience-scoped capabilities to launched processes. Keep reusable bearer material in process memory or the OS credential store. Resolve inherited policy with explicit deny precedence on every call; `manage` is structural only, and policy/ownership/grants/reserved roles/purge remain user-only. Clients cannot claim reserved roles, access siblings, mutate grants, or retain valid handles after lock/revocation. Moving the Core preserves client-authored content but requires destination reapproval.

- [x] **Step 12: Implement and run the catalog benchmark**

Generate the deterministic fixture matrix from the spec: 5,000 live entries plus 500 tombstones; 25,000 live entries plus 2,500 tombstones that must serialize at or below 16 MiB; and, if the maximum-live fixture is smaller than 16 MiB, a separate 16-MiB serialized-catalog fixture with no more than 25,000 live entries. Measure the full durable commit path. Commit `docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json` with OS/CPU/RAM/storage/filesystem, 4-KiB durable-write p95, crypto/serialization versions, warm-up/sample counts, live/tombstone/total counts, serialized size, p50/p95/p99, bytes written, and pass/fail for the 100-ms medium gate, the maximum-live size gate, and both 250-ms maximum gates. A maximum-live fixture above 16 MiB fails the design and blocks cutover. Do not treat an unrecorded local timing as release evidence.

- [x] **Step 13: Run focused tests**

```powershell
cargo test -p anima-file-tools -p anima-corefs -p anima-core -p animus
uv run python scripts/check_codex_attribution.py
bun run --cwd apps/desktop prepare:release
uv run python scripts/check_corefs_release_notices.py --release-root apps/desktop/src-tauri/resources/.anima
cargo metadata --locked --format-version 1 --no-deps
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_envelope.py apps/server/tests/test_corefs_catalog.py apps/server/tests/test_corefs_filesystem.py apps/server/tests/test_corefs_tools.py apps/server/tests/test_corefs_rotation.py apps/server/tests/test_corefs_process_lock.py apps/server/tests/test_corefs_clients.py -q
```

Expected: PASS locally, followed by a green `corefs-provenance` pull-request workflow from a clean checkout with no sibling Codex directory.

- [x] **Step 14: Commit CoreFS**

```powershell
git add Cargo.toml Cargo.lock THIRD_PARTY_NOTICES.md third_party/licenses/Apache-2.0.txt third_party/notices/openai-codex-NOTICE.txt scripts/check_codex_attribution.py scripts/check_corefs_release_notices.py scripts/prepare-desktop-release.ts .github/workflows/corefs-provenance.yml apps/desktop/src-tauri/tauri.conf.json packages/anima-file-tools packages/anima-corefs packages/anima-core/Cargo.toml packages/anima-core/src/ffi.rs apps/animus/Cargo.toml apps/animus/src/tools/files.rs apps/animus/src/tools/files apps/animus/src/tools/mod.rs apps/server/src/anima_server/services/corefs apps/server/src/anima_server/services/core.py apps/server/src/anima_server/schemas/corefs.py apps/server/src/anima_server/api/routes/corefs.py apps/server/src/anima_server/main.py apps/server/src/anima_server/services/agent/tools.py apps/server/scripts/benchmark_corefs_catalog.py docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json apps/server/tests/test_corefs_envelope.py apps/server/tests/test_corefs_catalog.py apps/server/tests/test_corefs_filesystem.py apps/server/tests/test_corefs_tools.py apps/server/tests/test_corefs_rotation.py apps/server/tests/test_corefs_process_lock.py apps/server/tests/test_corefs_clients.py
git -c commit.gpgsign=false commit -m "core: add Core Filesystem"
```

## Task 3: Machine-local Runtime and progressive indexing

**Ticket:** `PCF-003`  
**Depends on:** `PCF-002`
**Completed:** 2026-08-01.

**Files:**
- Create: `apps/server/src/anima_server/models/corefs_runtime.py`
- Create: `apps/server/src/anima_server/services/corefs/indexer.py`
- Create: `apps/server/src/anima_server/services/corefs/migration.py`
- Create: `apps/server/src/anima_server/services/corefs/legacy_runtime.py`
- Create: `apps/server/src/anima_server/services/corefs/instance_registry.py`
- Create: `apps/server/src/anima_server/schemas/corefs_security.py`
- Create: `apps/server/src/anima_server/api/routes/corefs_security.py`
- Create: `apps/server/alembic_runtime/versions/20260712_0001_add_corefs_index.py`
- Create: `apps/desktop/src/context/CoreFSReadinessContext.tsx`
- Modify: `apps/server/src/anima_server/config.py`
- Modify: `apps/server/src/anima_server/db/runtime.py`
- Modify: `apps/server/src/anima_server/db/pg_lifecycle.py`
- Modify: `apps/server/src/anima_server/main.py`
- Modify: `apps/server/src/anima_server/services/sessions.py`
- Modify: `apps/server/src/anima_server/api/routes/auth.py`
- Modify: `apps/server/src/anima_server/services/health/checks.py`
- Modify: `apps/server/src/anima_server/services/health/event_logger.py`
- Modify: `apps/server/src/anima_server/services/anima_core_retrieval.py`
- Modify: `apps/server/src/anima_server/models/runtime.py`
- Modify: `apps/server/src/anima_server/models/runtime_memory.py`
- Modify: `apps/server/src/anima_server/models/pending_memory_op.py`
- Modify: `apps/server/src/anima_server/services/agent/candidate_ops.py`
- Modify: `apps/server/src/anima_server/services/agent/pending_ops.py`
- Modify: `apps/server/src/anima_server/services/agent/consolidation.py`
- Modify: `apps/server/src/anima_server/services/agent/soul_writer.py`
- Modify: `apps/server/src/anima_server/api/routes/corefs.py`
- Modify: `apps/desktop/src-tauri/src/lib.rs`
- Modify: `apps/desktop/src/App.tsx`
- Modify: `apps/desktop/src/lib/api.ts`
- Modify: `packages/api-client/src/client.ts`
- Modify: `packages/api-client/src/types.ts`
- Modify: `apps/desktop/src/pages/settings/SecuritySettings.tsx`
- Modify: `package.json`
- Test: `apps/server/tests/test_corefs_indexer.py`
- Test: `apps/server/tests/test_runtime_db.py`
- Test: `apps/server/tests/test_health_startup.py`
- Test: `apps/server/tests/test_corefs_legacy_runtime.py`
- Test: `apps/server/tests/test_corefs_path_inventory.py`
- Test: `apps/server/tests/test_corefs_runtime_privacy.py`
- Test: `apps/server/tests/test_corefs_instance_registry.py`
- Test: `apps/server/tests/test_corefs_security_api.py`
- Test: `apps/desktop/tests/corefs-readiness.test.ts`
- Test: `apps/desktop/tests/corefs-key-rotation.test.ts`

- [x] **Step 1: Write failing runtime-location tests**

Assert PostgreSQL paths resolve under platform app data as `cores/<core-id>/instances/<local-instance-id>/runtime/pg_data`, never under `.anima/`, and copied Core manifests contain no machine path. A machine-local registry/lease binds `core_id` plus canonical resolved Core path and filesystem identity to one local instance. A moved (same filesystem identity) Core rebinds safely; a divergent copy with the same `core_id` receives a new local instance/runtime or is refused while the source lease is live, never shares PostgreSQL/index state. Cover same-machine transfer destination, stale lease, moved path, simultaneous clones, and explicit fork/rebuild.

- [x] **Step 2: Write failing readiness/index privacy tests**

Cover locked/opening/catalog-loading/catalog-ready-degraded/text-indexing/semantic-indexing/ready states, per-family failures, resume/cancel semantics, blind-token lookup, raw fresh-target PostgreSQL/runtime-disk scans for seeded message/chunk/OCR/source/candidate/pending-op plaintext, and teardown that clears plaintext indexes, semantic vectors, search subkeys, runtime-sealing keys, and query state on lock/logout/process shutdown. The quarantined legacy source may contain old plaintext until PCF-008, but it is never reused as the fresh target and is deleted after the forward-only marker.

- [x] **Step 3: Relocate active and legacy runtime paths**

Keep explicit `ANIMA_RUNTIME_DATABASE_URL` override behavior. Resolve the machine-local instance lease before opening PostgreSQL. With PostgreSQL stopped, move or copy-verify-delete an existing `.anima/runtime/pg_data` into platform app data under the bound local instance at `cores/<core-id>/instances/<local-instance-id>/legacy-runtime-source/pg_data`, record the source in migration state, and never include it in Core copy/export. Continue using it only as the legacy source until cutover; do not discard messages/assets before their converters pass.

Also move derived `.anima/indices` into `cores/<core-id>/instances/<local-instance-id>/cache/indices`, health `.anima/logs` into that instance's `health-logs`, and Tauri `.anima/runtime-daemon*` files into the machine-wide platform app-data daemon directory. All blind tokens, index checkpoints, caches, runtime logs, and migration journals are instance-scoped. For explicit `ANIMA_RUNTIME_DATABASE_URL`, atomically claim/verify an instance-binding row before migrations or queries and reject a URL already bound to another live/divergent Core instance. Add a static/path-contract test that only manifest/lock, `soul/`, `fs/`, `objects/`, and approved recovery material may be written under the Core root after cutover.

- [x] **Step 4: Add runtime catalog/checkpoint/blind-token models**

Persist only opaque IDs, hashes, revisions, statuses, index versions, progress, HMAC tokens, and a durable `CoreFSMigrationJournal` containing converter/source IDs, batch cursors, status, checksums, and errors without plaintext bodies. Add an application-layer runtime sealing service using `HKDF-SHA256(SQLCipher Soul key, salt=local-instance-id, info="anima-runtime-seal-v1")`; the key exists only after unlock and is never persisted. Crash-durable sensitive operational payloads such as memory candidates/pending Soul operations use authenticated sealed ciphertext with row type/ID/owner AAD plus minimal routing metadata. Rebuildable document chunks, OCR, source spans, knowledge bodies, previews, and vectors are process-memory only. Do not add plaintext body/title/preview/chunk/vector columns for Core content, and add schema/inventory tests that force every existing sensitive Runtime column to be removed, nulled and scrubbed in the fresh cutover database, or explicitly sealed with a documented retention need.

- [x] **Step 5: Implement staged reconciliation**

Follow `fs/HEAD`; authenticate the named catalog; publish catalog readiness first; reuse safe catalog/blind tokens; rebuild decrypted text and semantic structures in memory after every unlock.

- [x] **Step 6: Complete FRK/blind-index rotation**

Switch blind-token generations only after new tokens are complete, reject mixed generations, verify pending-FRK `fs/HEAD` recovery, and enforce old-root retirement criteria across retained catalogs and verified backups.

- [x] **Step 7: Add the operable key-rotation API and Security UI**

Create authenticated status/rotate/resume endpoints under `corefs_security`. Rotation accepts the recovery phrase only in the request body of the active unlock session, never persists/logs it, and verifies both recovery/password wrappers before `fs/HEAD`. Return active/pending/decrypt-only FRK versions, committed catalog generation, blind-index generation/progress, passphrase/recovery reopen results, errors, and old-key retirement safety. Wire `packages/api-client` and `SecuritySettings.tsx`; add API/UI tests for wrong recovery phrase, interrupted resume, mixed-token prevention, and retirement gating.

- [x] **Step 8: Implement lock/session teardown**

Wire logout, explicit Core lock, unlock-session expiry, and FastAPI shutdown to one `clear_unlocked_state(core_id)` path. Clear in-memory plaintext documents, ranks, vectors, blind-search subkeys, runtime-sealing keys, decrypted FRKs/Object DEKs, and outstanding query state; subsequent search/read or sealed operational-payload access must return locked until re-unlock.

- [x] **Step 9: Add progress and degraded readiness APIs/events**

Expose counts, phase, family, capabilities, retryability, and error summaries without private text.

- [x] **Step 10: Wire desktop readiness context and test runner**

Allow navigation at catalog readiness; show partial/degraded search state without blocking the entire app. Add root `test:desktop` script as `bun test apps/desktop/tests`.

- [x] **Step 11: Verify runtime relocation, deletion/rebuild, rotation UI, lock purge, and privacy**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_indexer.py apps/server/tests/test_corefs_legacy_runtime.py apps/server/tests/test_corefs_path_inventory.py apps/server/tests/test_corefs_instance_registry.py apps/server/tests/test_corefs_runtime_privacy.py apps/server/tests/test_corefs_security_api.py apps/server/tests/test_runtime_db.py apps/server/tests/test_health_startup.py -q
bun test apps/desktop/tests/corefs-readiness.test.ts apps/desktop/tests/corefs-key-rotation.test.ts
bun run --cwd apps/desktop build
cargo check -p desktop
```

Expected: backend PASS and desktop build succeeds.

- [x] **Step 12: Commit runtime/indexing/security UI**

```powershell
git add apps/server/src/anima_server/models/corefs_runtime.py apps/server/src/anima_server/models/runtime.py apps/server/src/anima_server/models/runtime_memory.py apps/server/src/anima_server/models/pending_memory_op.py apps/server/src/anima_server/services/corefs/indexer.py apps/server/src/anima_server/services/corefs/migration.py apps/server/src/anima_server/services/corefs/legacy_runtime.py apps/server/src/anima_server/services/corefs/instance_registry.py apps/server/src/anima_server/services/agent/candidate_ops.py apps/server/src/anima_server/services/agent/pending_ops.py apps/server/src/anima_server/services/agent/consolidation.py apps/server/src/anima_server/services/agent/soul_writer.py apps/server/src/anima_server/schemas/corefs_security.py apps/server/src/anima_server/api/routes/corefs_security.py apps/server/alembic_runtime/versions/20260712_0001_add_corefs_index.py apps/server/src/anima_server/config.py apps/server/src/anima_server/db/runtime.py apps/server/src/anima_server/db/pg_lifecycle.py apps/server/src/anima_server/main.py apps/server/src/anima_server/services/sessions.py apps/server/src/anima_server/api/routes/auth.py apps/server/src/anima_server/services/health/checks.py apps/server/src/anima_server/services/health/event_logger.py apps/server/src/anima_server/services/anima_core_retrieval.py apps/server/src/anima_server/api/routes/corefs.py apps/desktop/src-tauri/src/lib.rs packages/api-client/src/client.ts packages/api-client/src/types.ts apps/desktop/src/context/CoreFSReadinessContext.tsx apps/desktop/src/pages/settings/SecuritySettings.tsx apps/desktop/src/App.tsx apps/desktop/src/lib/api.ts package.json apps/server/tests/test_corefs_indexer.py apps/server/tests/test_corefs_legacy_runtime.py apps/server/tests/test_corefs_path_inventory.py apps/server/tests/test_corefs_instance_registry.py apps/server/tests/test_corefs_runtime_privacy.py apps/server/tests/test_corefs_security_api.py apps/server/tests/test_runtime_db.py apps/server/tests/test_health_startup.py apps/desktop/tests/corefs-readiness.test.ts apps/desktop/tests/corefs-key-rotation.test.ts
git -c commit.gpgsign=false commit -m "runtime: rebuild CoreFS indexes after unlock"
```

PR #127 implementation head `f0991e38` passed all required checks and received a
clean focused exact-head Codex review after full pagination found zero unresolved
consequential threads. The multi-login selection, stale-span lifecycle,
eval-reset live-vector eviction, concurrent legacy-sealing, mixed semantic
dimension, linked legacy-tree root, failed-config-persistence, untagged legacy
vector, exact source-identity, packaged Core/Runtime layout, durable path-oracle,
and Soul-only navigation gaps are repaired. Task 3 is complete; PCF-004 and
PCF-005 are dependency-eligible but remain unclaimed in backlog.

## Task 4: Diary, folders, drafts, and notes vertical slice

**Ticket:** `PCF-004`  
**Depends on:** `PCF-003`

> **Approved protocol follow-up:** The bounded, crash-resumable preparation work needed to complete this slice is specified in `docs/superpowers/specs/2026-08-02-corefs-resumable-preparation-design.md` and sequenced in `docs/superpowers/plans/2026-08-02-corefs-resumable-preparation.md`. That dedicated plan supersedes the aggregate whole-corpus transport described in Step 3 below; all other Task 4 product acceptance remains in force.

> **Approved evidence sequencing:** PCF-004 closes after the packaged cleanup
> implementation, local validation, and independent review. PCF-008 inherits
> the protected final signed Windows/macOS/DEB/RPM replacement-install runs and
> exact artifact digests as a mandatory pre-cutover/first-release gate. This
> defers the paid execution only; it does not waive or weaken the release gate.

**Files:**
- Modify: `packages/anima-corefs/src/id.rs`
- Modify: `packages/anima-corefs/src/logical/mod.rs`
- Modify: `packages/anima-corefs/src/logical/backend.rs`
- Modify: `packages/anima-corefs/src/logical/service.rs`
- Modify: `packages/anima-corefs/src/logical/wire.rs`
- Modify: `packages/anima-corefs/src/logical/mutation.rs`
- Create: `packages/anima-corefs/src/logical/mutation/converter.rs`
- Modify: `packages/anima-core/src/ffi.rs`
- Modify: `apps/server/src/anima_server/services/diary.py`
- Modify: `apps/server/src/anima_server/api/routes/diary.py`
- Modify: `apps/server/src/anima_server/schemas/diary.py`
- Modify: `apps/server/src/anima_server/services/corefs/formats.py`
- Modify: `apps/server/src/anima_server/services/corefs/migration.py`
- Modify: `apps/desktop/src/pages/Journal.tsx`
- Modify: `apps/desktop/src/pages/journal/content.ts`
- Modify: `apps/desktop/src/pages/journal/html.ts`
- Modify: `apps/desktop/src/lib/api.ts`
- Test: `apps/server/tests/test_diary_api.py`
- Test: `apps/server/tests/test_corefs_diary_migration.py`
- Test: `apps/server/tests/test_corefs_notes.py`
- Test: `apps/desktop/tests/journal-corefs.test.ts`
- Test: `apps/desktop/tests/journal-content.test.ts`
- Test: `apps/desktop/tests/journal-html.test.ts`
- Test: `packages/anima-corefs/tests/logical_snapshot.rs`
- Test: `packages/anima-corefs/tests/logical_wire.rs`

- [ ] **Step 1: Add failing logical-format and folder-role tests**

Cover versioned diary HTML sanitization, first-class stable IDs/empty/custom folders, `core.journal` and `core.notes` role resolution across rename/move/restart, default Journal and Notes `owner=user` plus `agentAccess=write`, deny inheritance, cover/attachment/extracted-inline-media CoreFS URIs, note Markdown/sanitized HTML, and encrypted draft objects. Prove sanitized diary HTML preserves the current Tiptap formatting contract while canonical bodies reject scripts, event handlers, unsafe URLs, and residual `data:` payloads.

- [ ] **Step 2: Add failing diary migration tests**

Seed SQLCipher diary folders/entries/attachments with plain-text bodies, rich HTML, inline base64 images, duplicate inline images, an empty folder, a binary cover, attachment-only entries, and cover-only entries. Convert plain text into escaped HTML paragraphs, sanitize existing HTML with the current versioned allowlist, extract and deduplicate valid inline media under MIME/size limits, replace it with stable CoreFS URIs, and reject malformed/oversized payloads without partial publication. Verify IDs, hashes, formatting, references, logical paths, API parity, zero canonical `data:` URLs, and idempotent reruns.

- [ ] **Step 3: Implement diary/note codecs, folder migration, and converter**

Convert each legacy diary folder to a first-class CoreFS folder with its stable ID, hierarchy, ordering, and metadata. Use sanitized HTML plus typed metadata as the canonical diary format so existing Tiptap formatting is not downgraded to Markdown. Reuse the desktop sanitizer contract on the server through one versioned allowlist; extract inline `data:` images into encrypted CoreFS binary objects and publish their diary references atomically. Bind the Journal app to the unique `core.journal` role and standalone notes to the unique `core.notes` role rather than display paths; both roots default to `owner=user` and `agentAccess=write`. Private diary and note content remains writable by ANIMA unless the user explicitly lowers access. Keep legacy reads behind the CoreFS layout version until converted and verified. Never dual-authority write after the slice activates.

Expose one sealed session-scoped validation-batch converter rather than unfreezing public CoreFS mutation. It must validate native opaque IDs, format/kind pairs, graph references, explicit root policy, stable-role uniqueness, object revision preconditions, and an exact expected validation head; prepare all encrypted revisions and publish exactly one validation generation. Add a deterministic domain-separated migration-ID helper plus read-only stable-role resolution. Ordinary public mutation remains frozen until PCF-008.

- [ ] **Step 4: Prepare diary service/routes for CoreFS behind the cutover gate**

Preserve existing response schemas and authorization checks so the desktop API contract does not change unnecessarily. Return resolvable CoreFS media references only through an unlocked authorized session, and update the Journal renderer/content helpers so rich HTML, covers, attachments, and extracted inline media behave as they do today. Before PCF-008, converters write only the inactive validation catalog and routes keep legacy authority; the CoreFS adapter becomes writable authority only through the global cutover state machine.

- [ ] **Step 5: Stage Journal drafts for encrypted CoreFS cutover**

Define versioned encrypted Core draft objects with revision preconditions and include existing local drafts in the inactive validation catalog. Before PCF-008, keep legacy localStorage drafts available because interactive CoreFS mutation remains frozen. At cutover, remove a legacy localStorage draft only after its encrypted server migration/save is verified.

- [ ] **Step 6: Add note access through CoreFS**

Provide format validation and generic CoreFS/agent access through the stable `core.notes` folder ID; prove rename/move/restart resolution and do not invent a full notes UI in this slice.

- [ ] **Step 7: Run backend and desktop validation**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_diary_api.py apps/server/tests/test_corefs_diary_migration.py apps/server/tests/test_corefs_notes.py -q
bun test apps/desktop/tests/journal-corefs.test.ts apps/desktop/tests/journal-content.test.ts apps/desktop/tests/journal-html.test.ts
bun run --cwd apps/desktop build
```

- [ ] **Step 8: Commit diary/notes slice**

```powershell
git add apps/server/src/anima_server/services/diary.py apps/server/src/anima_server/api/routes/diary.py apps/server/src/anima_server/schemas/diary.py apps/server/src/anima_server/services/corefs apps/desktop/src/pages/Journal.tsx apps/desktop/src/pages/journal/content.ts apps/desktop/src/pages/journal/html.ts apps/desktop/src/lib/api.ts apps/server/tests/test_diary_api.py apps/server/tests/test_corefs_diary_migration.py apps/server/tests/test_corefs_notes.py apps/desktop/tests/journal-corefs.test.ts apps/desktop/tests/journal-content.test.ts apps/desktop/tests/journal-html.test.ts
git -c commit.gpgsign=false commit -m "diary: move portable writing to encrypted Core objects"
```

## Task 5: Canonical threads, messages, and transcript merge

**Ticket:** `PCF-005`  
**Depends on:** `PCF-003`

**Files:**
- Create: `apps/server/src/anima_server/services/corefs/messages.py`
- Modify: `apps/server/src/anima_server/api/routes/chat.py`
- Modify: `apps/server/src/anima_server/api/routes/threads.py`
- Modify: `apps/server/src/anima_server/services/agent/thread_manager.py`
- Modify: `apps/server/src/anima_server/services/agent/service.py`
- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Modify: `apps/server/src/anima_server/services/agent/eager_consolidation.py`
- Modify: `apps/server/src/anima_server/services/agent/transcript_archive.py`
- Modify: `apps/server/src/anima_server/services/agent/transcript_search.py`
- Modify: `apps/server/src/anima_server/services/corefs/migration.py`
- Test: `apps/server/tests/test_corefs_messages.py`
- Test: `apps/server/tests/test_corefs_conversation_migration.py`
- Test: `apps/server/tests/test_multi_thread.py`
- Test: `apps/server/tests/test_p5_transcript_archive.py`

- [x] **Step 1: Write failing message-event/segment tests**

Cover the unique `core.conversations` root across rename/move/restart, default `owner=shared` plus `agentAccess=manage`, stable thread/message IDs, version preconditions, terminal delete, stale edit conflict, 256-event/1-MiB rollover, sequence-over-timestamp ordering, hash chain, corrupt segment gaps, and concurrent tail retry.

- [x] **Step 2: Write failing canonical-projection tests**

Prove visible user/final-assistant blocks and attachment CoreFS URIs survive while system prompts, memory injection, tool wrappers/results, thinking, trace, usage, and retrieval internals do not.

- [x] **Step 3: Implement message events and segment codecs**

Use one implementation for live append, edit/delete, migration, transcript import, and display projection.

- [x] **Step 4: Prepare CoreFS authority for visible messages/threads behind the cutover gate**

Bind canonical thread metadata and message segments beneath the unique `core.conversations` folder role/stable ID with default `owner=shared` and `agentAccess=manage`. Implement the canonical user/assistant visibility boundary and validate the shadow message projection, but keep legacy writes authoritative until PCF-008. Rewire the live service, transcript-search tool, eager consolidation, archive pruning, and thread routes behind the shared authority switch so none constructs or scans `.anima/transcripts` after cutover. After activation, `RuntimeMessage` retains Core message/event references, run/role/status/tool metadata, and only short-lived or sealed operational payloads; it never persists a duplicate plaintext visible body.

- [x] **Step 5: Rewire thread list/display/reactivation**

Resolve `core.conversations` by stable role then folder ID and read canonical thread/message objects directly; prove identical thread list/display/reactivation after rename, move, and restart. Do not rehydrate archived history into PostgreSQL as authority.

- [x] **Step 6: Implement active/archive migration and deduplication**

Merge legacy SQLCipher rows, PostgreSQL runtime messages, and encrypted transcripts using stable ID or deterministic fallback identity. Quarantine conflicts/unknown roles.

- [x] **Step 7: Run focused conversation tests**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_messages.py apps/server/tests/test_corefs_conversation_migration.py apps/server/tests/test_multi_thread.py apps/server/tests/test_p5_transcript_archive.py -q
```

- [x] **Step 8: Commit conversation slice**

```powershell
git add apps/server/src/anima_server/services/corefs/messages.py apps/server/src/anima_server/api/routes/chat.py apps/server/src/anima_server/api/routes/threads.py apps/server/src/anima_server/services/agent/thread_manager.py apps/server/src/anima_server/services/agent/service.py apps/server/src/anima_server/services/agent/tools.py apps/server/src/anima_server/services/agent/eager_consolidation.py apps/server/src/anima_server/services/agent/transcript_archive.py apps/server/src/anima_server/services/agent/transcript_search.py apps/server/src/anima_server/services/corefs/migration.py apps/server/tests/test_corefs_messages.py apps/server/tests/test_corefs_conversation_migration.py apps/server/tests/test_multi_thread.py apps/server/tests/test_p5_transcript_archive.py
git -c commit.gpgsign=false commit -m "chat: make encrypted message segments canonical"
```

## Task 6: Gallery, attachments, and original documents

**Ticket:** `PCF-006`  
**Depends on:** `PCF-003`, `PCF-005`

**Files:**
- Modify: `apps/server/src/anima_server/services/images/store.py`
- Modify: `apps/server/src/anima_server/services/images/deletion.py`
- Modify: `apps/server/src/anima_server/services/images/indexing.py`
- Modify: `apps/server/src/anima_server/services/images/rag.py`
- Modify: `apps/server/src/anima_server/services/images/backfill.py`
- Modify: `apps/server/src/anima_server/services/agent/attachments.py`
- Modify: `apps/server/src/anima_server/services/agent/state.py`
- Modify: `apps/server/src/anima_server/services/agent/document_tools.py`
- Modify: `apps/server/src/anima_server/services/documents/store.py`
- Modify: `apps/server/src/anima_server/services/documents/indexing.py`
- Modify: `apps/server/src/anima_server/services/documents/rag.py`
- Modify: `apps/server/src/anima_server/services/documents/parsing.py`
- Modify: `apps/server/src/anima_server/services/documents/contextual.py`
- Modify: `apps/server/src/anima_server/services/documents/pdf_workflow.py`
- Modify: `apps/server/src/anima_server/services/documents/pdf_text.py`
- Modify: `apps/server/src/anima_server/services/ingestion/artifacts.py`
- Modify: `apps/server/src/anima_server/services/ingestion/sources.py`
- Modify: `apps/server/src/anima_server/services/ingestion/compiler.py`
- Modify: `apps/server/src/anima_server/services/ingestion/document_compiler.py`
- Modify: `apps/server/src/anima_server/services/ingestion/retrieval.py`
- Modify: `apps/server/src/anima_server/services/ingestion/okf.py`
- Modify: `apps/server/src/anima_server/services/ingestion/lint.py`
- Modify: `apps/server/src/anima_server/services/ingestion/adapters/text.py`
- Modify: `apps/server/src/anima_server/services/ingestion/adapters/web.py`
- Modify: `apps/server/src/anima_server/services/ingestion/adapters/documents.py`
- Modify: `apps/server/src/anima_server/services/ingestion/adapters/images.py`
- Modify: `apps/server/src/anima_server/api/routes/images.py`
- Modify: `apps/server/src/anima_server/api/routes/documents.py`
- Modify: `apps/server/src/anima_server/api/routes/consciousness.py`
- Modify: `apps/server/src/anima_server/api/routes/knowledge.py`
- Modify: `apps/server/src/anima_server/services/agent/tools.py`
- Modify: `apps/server/src/anima_server/services/corefs/migration.py`
- Test: `apps/server/tests/test_corefs_assets.py`
- Test: `apps/server/tests/test_corefs_document_migration.py`
- Test: `apps/server/tests/test_image_assets.py`
- Test: `apps/server/tests/test_image_deletion.py`
- Test: `apps/server/tests/test_agent_biography_preview.py`
- Test: `apps/server/tests/test_corefs_knowledge_sources.py`
- Test: `apps/server/tests/test_pdf_workflow.py`
- Test: `apps/server/tests/test_pdf_workflow_checkpoints.py`
- Test: `apps/server/tests/test_document_parsing.py`
- Test: `apps/server/tests/test_document_tools.py`
- Test: `apps/server/tests/test_contextual_rerank.py`
- Test: `apps/server/tests/test_html_ingestion.py`
- Test: `apps/server/tests/test_structured_document.py`
- Test: `apps/server/tests/test_web_fetch.py`
- Test: `apps/server/tests/test_knowledge_autocompile.py`
- Test: `apps/server/tests/test_retrieval_eval.py`

- [x] **Step 1: Add failing binary-object/reference tests**

Cover per-object DEKs, chunk-authenticated streaming/range reads, content hashes, truncation/reordering rejection, gallery metadata revisions, `core.gallery` stable-role resolution across rename/move/restart, chat/document/diary attachment reference counts, agent-avatar identity assets, and trash without host-path escape.

- [x] **Step 2: Add failing source migration tests**

Seed `runtime_image_assets`, annotations/links, `runtime_documents`, original uploads, pasted text/Markdown, captured web pages, source artifacts/spans, derived chunks, contextual blurbs, compiled concepts, and concept citations. Verify original user-owned bytes and every captured source needed for deterministic offline rebuild become canonical CoreFS objects. Enforce this current-field migration matrix:

- `RuntimeDocument` filename/title/storage-path metadata becomes private canonical metadata or safe Runtime hashes/CoreFS references; no legacy host path remains authoritative.
- `RuntimeDocumentChunk.content_text`, section title/path metadata, contextual blurb, previews, embeddings, and vectors become unlock-scoped in-memory derivations and are scrubbed from persistent Runtime.
- `RuntimeSourceArtifact.content_text`, raw HTML, normalized structured Markdown, and `RuntimeSourceSpan.content_text` become encrypted captured-source objects or unlock-scoped derivations according to the source contract; none remains plaintext Runtime data.
- `RuntimeKnowledgeConcept` title, description, `body_markdown`, and frontmatter plus `RuntimeKnowledgeConceptSource.quote_text` become unlock-scoped compiled projections backed by canonical captured sources; Runtime retains only safe opaque/hash/locator/progress metadata.

Delete Runtime, rebuild without network access, compare document/source/concept retrieval behavior, and scan the rebuilt persistent store for seeded plaintext markers.

- [x] **Step 3: Rewire image and attachment storage**

Implement adapters replacing direct `users/<id>/avatars`, `users/<id>/attachments/chat`, diary/image paths, and document storage paths with Core object streams and CoreFS URIs. Bind Gallery to the unique `core.gallery` folder role/stable ID rather than its display path. Treat the agent avatar as an encrypted identity asset referenced by `AgentProfile`, not as a SQLCipher blob or plaintext host file. Preserve API authorization and MIME/size validation. Keep adapters behind the inactive authority gate until PCF-008; pre-cutover feature writes remain legacy-authoritative while converters validate the shadow catalog.

- [x] **Step 4: Rewire document registration**

Register the original document as a canonical attachment object; runtime ingestion rows reference its object ID/revision and rebuild chunks/indexes. Change `documents/parsing.py`, PDF reindex, and text extraction to consume a typed bounded authenticated CoreFS byte/range source instead of a host `Path`. Rewire `agent/document_tools.py` to query the unlocked in-memory document index and hydrate canonical CoreFS/source content rather than reading persisted `RuntimeDocumentChunk.content_text`. Generate contextual blurbs in memory for the active unlock/index generation; never persist them in Runtime chunk metadata. Never materialize a decrypted normal temp file merely to satisfy a host-`Path` API.

- [x] **Step 5: Canonicalize imported knowledge sources**

Store pasted text/Markdown and, for web capture, both the original raw HTML and its normalized structured snapshot as encrypted source objects with source URI, fetch timestamp, content type, extractor/sanitizer version, and content hash. Keeping raw HTML permits future deterministic re-extraction without network refetch; the normalized snapshot preserves the exact imported revision. Runtime source rows point only to CoreFS IDs/revisions and safe progress metadata; refresh creates new canonical revisions rather than silently replacing history. Rework `ingestion/compiler.py` so compiled concept title/description/body/frontmatter, source spans, citation quote text, and chunks exist only in the active unlocked index/projection. Persist no compiled plaintext duplicate in PostgreSQL. The merged `documents/reranker.py`, `ingestion/structured.py`, `ingestion/html_extract.py`, and `ingestion/web_fetch.py` remain validation surfaces because they are pure/in-memory adapters; their regression tests must still pass, and any future persistence or host-path dependency moves them into the explicit migration inventory.

- [x] **Step 6: Reconcile conversation attachment links**

Ensure canonical message segments reference migrated gallery/document CoreFS URIs before runtime legacy links are discarded.

- [x] **Step 7: Run focused tests**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_assets.py apps/server/tests/test_corefs_document_migration.py apps/server/tests/test_corefs_knowledge_sources.py apps/server/tests/test_image_assets.py apps/server/tests/test_image_deletion.py apps/server/tests/test_image_retrieval_context.py apps/server/tests/test_agent_biography_preview.py apps/server/tests/test_pdf_workflow.py apps/server/tests/test_pdf_workflow_checkpoints.py apps/server/tests/test_document_parsing.py apps/server/tests/test_document_tools.py apps/server/tests/test_contextual_rerank.py apps/server/tests/test_html_ingestion.py apps/server/tests/test_structured_document.py apps/server/tests/test_web_fetch.py apps/server/tests/test_knowledge_autocompile.py apps/server/tests/test_retrieval_eval.py -q
```

- [x] **Step 8: Commit asset/document/knowledge-source slice**

```powershell
git add apps/server/src/anima_server/services/images apps/server/src/anima_server/services/agent/attachments.py apps/server/src/anima_server/services/agent/state.py apps/server/src/anima_server/services/agent/document_tools.py apps/server/src/anima_server/services/agent/tools.py apps/server/src/anima_server/services/documents apps/server/src/anima_server/services/ingestion/artifacts.py apps/server/src/anima_server/services/ingestion/sources.py apps/server/src/anima_server/services/ingestion/compiler.py apps/server/src/anima_server/services/ingestion/document_compiler.py apps/server/src/anima_server/services/ingestion/retrieval.py apps/server/src/anima_server/services/ingestion/okf.py apps/server/src/anima_server/services/ingestion/lint.py apps/server/src/anima_server/services/ingestion/adapters apps/server/src/anima_server/api/routes/images.py apps/server/src/anima_server/api/routes/documents.py apps/server/src/anima_server/api/routes/consciousness.py apps/server/src/anima_server/api/routes/knowledge.py apps/server/src/anima_server/services/corefs/migration.py apps/server/tests/test_corefs_assets.py apps/server/tests/test_corefs_document_migration.py apps/server/tests/test_corefs_knowledge_sources.py apps/server/tests/test_image_assets.py apps/server/tests/test_image_deletion.py apps/server/tests/test_agent_biography_preview.py apps/server/tests/test_pdf_workflow.py apps/server/tests/test_pdf_workflow_checkpoints.py apps/server/tests/test_document_parsing.py apps/server/tests/test_document_tools.py apps/server/tests/test_contextual_rerank.py apps/server/tests/test_html_ingestion.py apps/server/tests/test_structured_document.py apps/server/tests/test_web_fetch.py apps/server/tests/test_knowledge_autocompile.py apps/server/tests/test_retrieval_eval.py
git -c commit.gpgsign=false commit -m "content: move user assets into encrypted Core objects"
```

## Task 7: Account profile, tasks, preferences, and credentials

**Ticket:** `PCF-007`  
**Depends on:** `PCF-004`, `PCF-006`

**Files:**
- Create: `apps/server/src/anima_server/services/credentials.py`
- Create: `apps/server/src/anima_server/api/routes/credentials.py`
- Create: `apps/anima-mod/src/security/credential-broker.ts`
- Create: `docs/architecture/system/portable-state-inventory.md`
- Modify: `apps/server/pyproject.toml`
- Modify: `uv.lock`
- Modify: `Cargo.lock`
- Modify: `apps/server/src/anima_server/db/user_store.py`
- Modify: `apps/server/src/anima_server/services/core.py`
- Modify: `apps/server/src/anima_server/main.py`
- Modify: `apps/server/src/anima_server/services/auth.py`
- Modify: `apps/server/src/anima_server/api/routes/auth.py`
- Modify: `apps/server/src/anima_server/api/routes/users.py`
- Modify: `apps/server/src/anima_server/api/routes/tasks.py`
- Modify: `apps/server/src/anima_server/api/routes/config.py`
- Modify: `apps/server/src/anima_server/api/routes/soul.py`
- Modify: `apps/server/src/anima_server/api/routes/presence.py`
- Modify: `apps/server/src/anima_server/api/routes/telegram.py`
- Modify: `apps/server/src/anima_server/services/presence_config.py`
- Modify: `apps/server/src/anima_server/models/presence.py`
- Modify: `apps/server/src/anima_server/models/links.py`
- Modify: `apps/server/src/anima_server/services/corefs/migration.py`
- Modify: `apps/desktop/src/lib/theme.ts`
- Modify: `apps/desktop/src/lib/background.ts`
- Modify: `apps/desktop/src/lib/preferences.ts`
- Modify: `apps/desktop/src/context/AsciiSettingsContext.tsx`
- Modify: `apps/desktop/src/pages/settings/AiSettings.tsx`
- Modify: `apps/desktop/src/lib/daemon.ts`
- Modify: `apps/desktop/src/pages/settings/DaemonSettings.tsx`
- Create: `apps/desktop/src/pages/settings/CoreFSAccessSettings.tsx`
- Modify: `packages/api-client/src/client.ts`
- Modify: `packages/api-client/src/types.ts`
- Modify: `apps/desktop/src-tauri/Cargo.toml`
- Modify: `apps/desktop/src-tauri/src/lib.rs`
- Modify: `apps/local-runtime-daemon/Cargo.toml`
- Modify: `apps/local-runtime-daemon/src/main.rs`
- Modify: `apps/anima-mod/src/core/types.ts`
- Modify: `apps/anima-mod/src/core/context.ts`
- Modify: `apps/anima-mod/src/core/store.ts`
- Modify: `apps/anima-mod/src/management/config-service.ts`
- Modify: `apps/anima-mod/mods/google/oauth.ts`
- Modify: `apps/anima-mod/mods/google/mod.ts`
- Test: `apps/server/tests/test_corefs_account_migration.py`
- Test: `apps/server/tests/test_corefs_preferences.py`
- Test: `apps/server/tests/test_corefs_state_inventory.py`
- Test: `apps/server/tests/test_tasks_api.py`
- Test: `apps/server/tests/test_auth.py`
- Test: `apps/server/tests/test_credentials_api.py`
- Test: `apps/desktop/tests/settings-storage-classification.test.ts`
- Test: `apps/desktop/tests/daemon-credential-migration.test.ts`
- Test: `apps/desktop/tests/corefs-client-access.test.ts`
- Test: `apps/anima-mod/tests/management/config-service.test.ts`
- Test: `apps/anima-mod/mods/google/mod.test.ts`

- [x] **Step 1: Generate the persisted-setting inventory fixture**

Create a checked row/field matrix for every SQLCipher model/table/column, runtime table/column, localStorage/sessionStorage key, persisted server setting, Tauri/daemon app-data file, anima-mod SQLite/YAML/store value, and credential call. At minimum classify: `users` fields -> account profile/opaque Soul owner; `user_keys` -> manifest roots or `soul_keyslots`; `presence_configs` flags/custom instruction -> portable preferences; Telegram/Discord chat/channel links -> machine-local integration registry; provider/model/URLs -> device runtime config; provider/connector/daemon/OAuth secrets -> OS credentials; durable AgentProfile/SelfModel/memory/emotional/growth fields -> Soul. Treat mixed rows field-by-field: `AgentProfile.setup_complete` is portable onboarding/app state, while `SelfModelBlock.needs_regeneration` and `MemoryEpisode.needs_regeneration` are disposable runtime work flags rather than Soul identity. Fail tests when any persisted key, table, or column is absent from the approved matrix.

- [x] **Step 2: Add failing account/task/preference migration tests**

Cover encrypted account profile, opaque owner ID, no plaintext username index, task JSON objects, portable preference mapping, device-local exclusions, and session-only state.

- [x] **Step 3: Implement OS credential service**

Implement one OS-credential boundary across Python, Tauri/local-runtime-daemon, and anima-mod. Python owns provider credentials and an authenticated loopback credential broker with short-lived audience-scoped capabilities for the mod process; it never exposes a generic secret-read route to the browser. Tauri and the local daemon share a platform keyring entry for the daemon control token. `ConfigService` stores only opaque credential references for schema fields marked `secret`, and Google OAuth uses a dedicated `SecretStore`/broker path rather than `mod_store`. Fail closed with a clear configuration error when no secure backend is available; do not silently fall back to SQLite, YAML, files, environment persistence, or localStorage.

- [x] **Step 4: Migrate local account unlock**

Authenticate by unwrapping manifest keyslots, open SQLCipher, load Soul domain keys, then read the encrypted account-profile object. Remove manifest `user_index`; remembered username remains optional device-local convenience.

- [x] **Step 5: Rewire task routes to CoreFS behind the cutover gate**

Preserve task API schemas. Before PCF-008, routes continue legacy-authoritative writes while the converter validates shadow JSON objects; the shared authority switch activates CoreFS writes only with the global cutover marker.

- [x] **Step 6: Migrate portable preferences and drafts**

Use the spec's key-by-key classification. Background media must be explicitly imported as a Core attachment; host paths stay device-local.

- [x] **Step 7: Move provider/runtime config and secrets to their destinations**

Move `.anima/runtime-config.json` into platform app data with copy-verify-delete migration. Remove manifest device/runtime fields such as `runtime_database_engine`, requested/previous/target engine, requester, and `runtime_migration_state`; migrate them to the machine-local runtime registry, while portable encryption/layout/cutover state remains in the manifest. Provider/model/URLs remain machine-local config; secrets go to OS credentials; presence preferences and onboarding completion move to Core preference/account objects; regeneration flags move to Runtime work state; Telegram/Discord link identifiers move to the machine-local runtime integration registry and require relinking after transfer. Migrate the daemon token from legacy file/localStorage into the shared OS credential entry and delete both old copies after verification. Convert anima-mod secret config rows and Google OAuth token objects to credential references, then scrub plaintext SQLite/YAML values. Migrate legacy `users/<id>/soul.md` into the canonical Soul `user_directive`/persona section when not already represented, verify content hash, then remove the plaintext file. No sensitive value or reusable token remains in browser storage.

- [x] **Step 8: Add client folder-access settings**

Expose registered clients/mods, verified package identity, declared namespaced roles, current folder-scoped read/write/manage grants, and last-use audit metadata without body content. Require explicit confirmation to grant or expand scope; allow immediate downgrade/revocation. Resolve folders by stable ID/role so rename/move does not invalidate a same-device grant. Show that grants are device-local and require reapproval after transfer. Never expose a control that lets a client grant itself access or claim `core.*` roles.

- [x] **Step 9: Verify neutral pre-unlock behavior**

Locked UI uses OS locale/accessibility and neutral ANIMA branding; private profile/avatar appears only after content unlock.

- [x] **Step 10: Run backend and desktop tests**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_account_migration.py apps/server/tests/test_corefs_preferences.py apps/server/tests/test_corefs_state_inventory.py apps/server/tests/test_credentials_api.py apps/server/tests/test_tasks_api.py apps/server/tests/test_auth.py apps/server/tests/test_recovery.py apps/server/tests/test_config_personas.py apps/server/tests/test_telegram_routes.py -q
bun test apps/desktop/tests/settings-storage-classification.test.ts apps/desktop/tests/daemon-credential-migration.test.ts apps/desktop/tests/corefs-client-access.test.ts
bun test apps/anima-mod/tests/management/config-service.test.ts apps/anima-mod/mods/google/mod.test.ts
bun run build:anima-mod
cargo test -p anima-local-runtime-daemon
cargo check -p desktop
bun run --cwd apps/desktop build
```

- [x] **Step 11: Commit account/settings slice**

```powershell
git add apps/server/pyproject.toml uv.lock Cargo.lock docs/architecture/system/portable-state-inventory.md apps/server/src/anima_server/services/credentials.py apps/server/src/anima_server/services/core.py apps/server/src/anima_server/main.py apps/server/src/anima_server/db/user_store.py apps/server/src/anima_server/services/auth.py apps/server/src/anima_server/api/routes/credentials.py apps/server/src/anima_server/api/routes/auth.py apps/server/src/anima_server/api/routes/users.py apps/server/src/anima_server/api/routes/tasks.py apps/server/src/anima_server/api/routes/config.py apps/server/src/anima_server/api/routes/soul.py apps/server/src/anima_server/api/routes/presence.py apps/server/src/anima_server/api/routes/telegram.py apps/server/src/anima_server/services/presence_config.py apps/server/src/anima_server/models/presence.py apps/server/src/anima_server/models/links.py apps/server/src/anima_server/services/corefs/migration.py apps/desktop/src/lib/theme.ts apps/desktop/src/lib/background.ts apps/desktop/src/lib/preferences.ts apps/desktop/src/context/AsciiSettingsContext.tsx apps/desktop/src/pages/settings/AiSettings.tsx apps/desktop/src/pages/settings/DaemonSettings.tsx apps/desktop/src/pages/settings/CoreFSAccessSettings.tsx apps/desktop/src/lib/daemon.ts packages/api-client/src/client.ts packages/api-client/src/types.ts apps/desktop/src-tauri/Cargo.toml apps/desktop/src-tauri/src/lib.rs apps/local-runtime-daemon/Cargo.toml apps/local-runtime-daemon/src/main.rs apps/anima-mod/src/security/credential-broker.ts apps/anima-mod/src/core/types.ts apps/anima-mod/src/core/context.ts apps/anima-mod/src/core/store.ts apps/anima-mod/src/management/config-service.ts apps/anima-mod/mods/google/oauth.ts apps/anima-mod/mods/google/mod.ts apps/server/tests/test_corefs_account_migration.py apps/server/tests/test_corefs_preferences.py apps/server/tests/test_corefs_state_inventory.py apps/server/tests/test_credentials_api.py apps/server/tests/test_tasks_api.py apps/server/tests/test_auth.py apps/server/tests/test_config_personas.py apps/server/tests/test_telegram_routes.py apps/desktop/tests/settings-storage-classification.test.ts apps/desktop/tests/daemon-credential-migration.test.ts apps/desktop/tests/corefs-client-access.test.ts apps/anima-mod/tests/management/config-service.test.ts apps/anima-mod/mods/google/mod.test.ts
git -c commit.gpgsign=false commit -m "core: separate portable account and app preferences"
```

## Task 8: Cutover, transfer, and first-release validation

**Ticket:** `PCF-008`  
**Depends on:** `PCF-001` through `PCF-007`

**Inherited signed-package gate:** Before the irreversible cutover event or any
cleanup-capable first-release publication, separately authorize and run the
triggerless protected desktop workflow against the final signed Windows,
macOS, DEB, and RPM artifacts. Every replacement-install, launch-target,
process-census, post-WebView capability, and source-first cleanup gate must
pass, and the exact artifact digests/results must be recorded. Failure or
missing evidence blocks PCF-008; it cannot be treated as a skipped CI check.

**Files:**
- Create: `apps/server/src/anima_server/services/corefs/transfer.py`
- Create: `apps/server/src/anima_server/schemas/corefs_transfer.py`
- Create: `apps/server/src/anima_server/api/routes/corefs_transfer.py`
- Modify: `apps/server/src/anima_server/services/corefs/migration.py`
- Modify: `apps/server/src/anima_server/db/session.py`
- Modify: `apps/server/src/anima_server/db/user_store.py`
- Modify: `apps/server/src/anima_server/services/storage.py`
- Modify: `apps/server/src/anima_server/services/vault.py`
- Modify: `apps/server/src/anima_server/services/anima_core_bindings.py`
- Modify: `apps/server/src/anima_server/api/routes/vault.py`
- Modify: `apps/server/src/anima_server/schemas/vault.py`
- Modify: `apps/server/src/anima_server/main.py`
- Create: `packages/anima-core/src/core_archive.rs`
- Modify: `packages/anima-core/src/capsule.rs` (legacy V1 import only)
- Modify: `packages/anima-core/src/integrity.rs`
- Modify: `packages/anima-core/src/ffi.rs`
- Modify: `packages/anima-core/src/lib.rs`
- Modify: `packages/api-client/src/client.ts`
- Modify: `packages/api-client/src/types.ts`
- Create: `apps/desktop/src/pages/settings/CoreTransferSettings.tsx`
- Modify: `apps/desktop/src/pages/settings/VaultSettings.tsx` (redirect/deprecate legacy UI)
- Modify: architecture/thesis/PRD docs listed in the design, including `docs/architecture/agent/document-processing.md` and `docs/architecture/agent/source-ingestion.md`
- Modify: `.github/workflows/desktop-draft-cleanup-authority.yml`
- Modify: `scripts/verify-desktop-release-contract.ts`
- Test: `apps/desktop/tests/desktop-release-contract.test.ts`
- Test: `apps/desktop/tests/journal-draft-cleanup-authority.test.ts`
- Test: `apps/server/tests/test_corefs_cutover.py`
- Test: `apps/server/tests/test_corefs_transfer.py`
- Test: `apps/server/tests/test_corefs_authority.py`
- Test: `apps/server/tests/test_corefs_soul_relocation.py`
- Test: `apps/server/tests/test_vault.py`
- Test: `apps/server/tests/test_health_integration.py`
- Test: `apps/desktop/tests/corefs-transfer.test.ts`

- [x] **Step 1: Write failing cutover-state tests**

Cover `migrating-write-frozen`, `corefs-validation-readonly`, `corefs-approved-pending-first-write`, authenticated first-write cutover marker, crash before manifest finalization, and forward-only rejection of legacy rollback.

- [ ] **Step 2: Write failing transfer tests**

Progress (2026-08-13): live Soul-bearing capture now checkpoints WAL, pins one
SQLite/SQLCipher read snapshot, writes an independently verified encrypted
online-backup database, and uses its private inventory hash as the
preflight-to-export fence. Full/CoreFS capture freezes `fs/HEAD`, rechecks the
native generation/catalog/source inventory after Soul capture, and binds the
native streaming session to that exact authenticated catalog while retaining
the object lease. WAL inclusion, temporary cleanup, concurrent catalog change,
and real SQLCipher-at-rest regressions pass. Multipart-set and backward-V1
cases remain open, so this step is intentionally not checked complete.

Cover closed cold copy; live write-barrier snapshots; SQLCipher checkpoint; catalog/GC pinning; reachable-object selection; no Runtime inclusion; `full`, `soul`, and `fs` artifact/key allowlists, scoped credential replacement, and recovery states; fixed-header/profile bounds; exact normative AAD tuple; pre-archive record hash; global nonce ordinal; destination capacity/writable/single-file-limit preflight; <=8-MiB buffers and <=32-MiB aggregate transfer working set excluding the fixed Argon2 workspace; `.partial` cleanup; single-file and FAT32-like multipart output; failure at every part/controller/directory publication boundary; disconnect/cancel; missing/reordered/mixed/tampered volumes; same-volume import capacity/staging; final-directory and active-Core registry-pointer activation failures; V1 CoreFS-reattachment rejection; destination hash/decrypt verification; rejection of incoherent live raw copies; same-machine duplicate-Core instance handling; and binary objects larger than the legacy 16-MiB section limit.

- [ ] **Step 3: Implement resumable migration orchestration and physical Soul relocation**

Preflight space/keys/schema; freeze writes; run all converters through the durable runtime migration journal; verify counts/hashes/references/API parity; build a separate fresh outside-Core runtime; expose accept/reject state. Close every SQLCipher engine and use copy-verify-flip to relocate the single-owner legacy `users/<legacy-id>/anima.db` (including a clean WAL checkpoint) to `.anima/soul/soul.db`. Atomically publish the new manifest path only after schema, page-integrity, decryptability, and deterministic retained-table hashes pass. Preserve the old encrypted file for rollback until the authenticated first CoreFS mutation; after the forward-only marker, remove the legacy `users/` database copy and prove no service can recreate it.

- [ ] **Step 4: Implement the single irreversible cutover event**

The first accepted mutation writes the authenticated catalog marker and publishes `fs/HEAD`. Startup finalizes manifest forward-only state if it crashes immediately afterward.

- [ ] **Step 5: Secure and retire the legacy PostgreSQL source after the marker**

Progress (2026-08-13): a machine-local recovery primitive now inventories the
stopped relocated legacy PostgreSQL tree twice, encrypts paths and contents in
bounded chunks under an OS-credential-held random key, authenticates the exact
inventory/footer with one monotonic nonce sequence, publishes create-only
outside the portable Core, and re-verifies the final bundle. Durable partial
publication resumes without overwrite. Plaintext retirement independently
requires forward-only CoreFS authority, stopped PostgreSQL, the authenticated
bundle, and a present fresh Runtime database. On the next startup after the
marker, embedded Runtime recovery runs before PostgreSQL selection, starts the
fresh data directory instead of the retained source, verifies its instance
binding and schema, and only then retires plaintext; an explicitly configured
fresh Runtime follows the same post-binding gate. First-mutation shutdown/
restart coordination remains open, so this step is intentionally not checked
complete.

Before enabling writes, create and verify an authenticated encrypted recovery bundle of `legacy-runtime-source` outside `.anima/` while retaining plaintext rollback source. After the marked first mutation makes rollback forward-only, stop the legacy server, switch to the fresh runtime, delete the plaintext legacy directory, and retain only the encrypted recovery bundle for the later cleanup release. A moved Core never includes either runtime form.

- [ ] **Step 6: Add exact transfer API and desktop flow**

Progress (2026-08-13): export plus non-activating restore staging are wired
through the authenticated API client and desktop UI. Restore rechecks the exact
archive/capacity/staging inputs at consumption, authenticates the complete
native inventory into a create-only same-volume sibling, and cleans every
failed/cancelled partial. Active-Core registry activation and retained-Core
rollback are now both exposed as authenticated restart-only intents, with
explicit rollback confirmation and no machine paths in the status contract.
CoreFS-only attachment attempts now return the stable V1
`corefs_reattachment_not_supported` conflict through the authenticated API and
client. Completed CoreFS-only imports now also expose bounded authenticated
stat/list/read browsing against the exact staged generation. One-request
compartment-limited unlocks authenticate retained wrappers under their original
source-scope AAD, forbid every foreign-purpose wrapper, and recheck authenticated
control-record hashes without persisting a recovery session or exposing the
staging path. A pre-cutover snapshot gets a serialized byte-exact temporary
validation-pointer alias from its authenticated HEAD, removed on every exit.
The desktop cannot attach or activate this recovery Core. A separately
confirmed replacement request now authenticates one current source wrapper,
unwraps only the FS compartment, creates fresh FS-scoped password and recovery
generations, independently reopens both before publication, and returns the
new recovery phrase once without retaining any request credential or phrase.
Keyslot inventory publishes before manifest authority and ordinary failures
restore both original control files byte-for-byte; a process crash can only
invalidate the disposable, non-activatable staging copy. Recovery-only
re-export now opens a credential-bound staged native context, uses only the
explicit archive-authenticated staged root and manifest, pins the filesystem
generation/object lease, rechecks control authority around streaming, and
publishes through the verified cancellable `.partial` flow without attachment
or activation. Multipart UI remains open, so this step is intentionally not
checked complete.

Implement `corefs_transfer` schemas/routes for local destination probe, estimate, prepare, progress, cancel, verify, import, and completion. Wire `packages/api-client` and `CoreTransferSettings.tsx` to present **Export ANIMA CORE** and **Restore ANIMA CORE** as the primary flow, with **Soul only** and **CoreFS only** under Advanced Recovery. Show write-barrier/checkpoint state, selected artifact kind, required/available export bytes, required/available same-volume import-staging bytes, detected single-file limit, single/multipart decision, bounded streaming progress, verification, and safe destination result. Soul-only recovery clearly labels degraded `filesystem_missing`; CoreFS-only recovery exposes authenticated browse/export and returns `corefs_reattachment_not_supported` for V1 attach attempts. Do not instruct users to drag-copy a live Core or run the live Core from removable media.

- [ ] **Step 7: Implement scalable vault/export/import**

Progress (2026-08-13): single-file V2 export/import primitives are in place,
including payload-kind record allowlists and key-material-scoped transient
manifest snapshots. Soul artifacts cannot carry FRK wrappers or filesystem
authority; CoreFS-only artifacts cannot carry SQLCipher root wrappers. Live
Soul is captured through a verified encrypted online backup, and full/CoreFS
streaming is bound to a frozen authenticated `fs/HEAD` plus exact native
generation/catalog hash under the object lease. Native multipart-set
authentication and backward V1 import remain open, so this step is
intentionally not checked complete.

Startup progress (2026-08-13): the machine-local active-Core registry is now
authenticated by an OS-credential-held key, resolved before the Core lock and
database bootstrap, and able to recover an interrupted full-restore pointer
swap while retaining the prior Core. Partial recovery artifacts are
structurally ineligible for activation. Product activation and rollback
commands are now restart-only and deliberately cannot change live resources.

Activation progress (2026-08-13): verified full restores can now create a
machine-local authenticated activation intent while the running pointer remains
unchanged. The next pre-resource startup consumes that intent through the
journaled rename/pointer/completion protocol and retains the old Core. Partial
artifacts remain ineligible. Retained-Core rollback now uses a separate
authenticated, explicitly confirmed restart intent; it exposes only identifier
metadata, re-verifies both selected Cores before the pointer transaction, and is
idempotent across a crash after the rollback pointer write.

Implement `anima_core_v2` in Rust as one streaming container with authenticated payload kind `full`, `soul`, or `fs`; keep `capsule.rs` only for backward V1 `anima_capsule` import. A full artifact includes manifest, active Soul, committed content catalogs/objects, required keyslots/recovery material, and the coherent `(soulGeneration, filesystemGeneration)` pair. Soul-only and CoreFS-only artifacts enforce compartment-specific record/key allowlists and restore respectively to `filesystem_missing` and restricted recovery/export mode. Every kind excludes Runtime, device config, and OS credentials.

Write a small typed encrypted manifest followed by a 64-bit-length, chunked sequence of selected encrypted records and an authenticated complete-inventory footer. Every implementation uses this exact derivation:

```text
argon = Argon2id(passphrase, salt=kdfSalt, time=4, memory=131072 KiB, parallelism=4, outputLength=32)
archiveKey = HKDF-SHA256(ikm=argon, salt=None, info="anima-core-archive-v2", outputLength=32)
```

The exact fixed-header field order is `magic`, `formatVersion`, `headerLength`, `cipherId`, `kdfId`, `kdfProfileId`, `kdfTimeCost`, `kdfMemoryKiB`, `kdfParallelism`, `kdfSalt[32]`, `archiveId`, `volumeSetId`, `payloadKind`, `volumeMode`, `declaredVolumeCount`, `chunkLimitBytes`, and `noncePrefix[4]`. Before Argon2, validate magic/version/header length, require the registered V2 cipher/KDF/profile and exact costs above, require the 32-byte salt and 8-MiB chunk limit, validate enum/count/ID encodings, and reject unknown/out-of-range values. The encrypted manifest repeats `archiveId`, `volumeSetId`, `payloadKind`, `volumeMode`, and `declaredVolumeCount`; reject any mismatch. Authenticate `headerHash = SHA-256(exact serialized fixed header)` in the manifest and every chunk. Pre-hash each pinned stable source as `recordHash = SHA-256(exact pre-archive record bytes)`, then stream the encryption pass.

Use the exact normative chunk AAD tuple: `headerHash`, `archiveId`, `volumeSetId`, `payloadKind`, `recordType`, `recordPath`, `recordOrdinal`, `recordHash`, `chunkIndex`, `chunkCount`, `plaintextOffset`, `plaintextLength`, `ciphertextLength`, `finalFlag`, and `volumeOrdinal`. Generate a random 32-bit nonce prefix per archive and append one monotonically increasing 64-bit global chunk ordinal that never resets across records/volumes; reject reuse/regression/overflow, and restart interrupted export only with a new archive ID/salt/key/prefix. Core object and SQLCipher encryption remain independently intact. Each source or ciphertext buffer is at most 8 MiB. Aggregate export/import streaming working memory is at most 32 MiB, excluding the fixed 128-MiB Argon2 workspace and fixed runtime/library overhead. Incrementally hash inventory/footer state; any disk spool remains encrypted/authenticated under `archiveKey` and is deleted with partial output on failure.

Use `volumeSetId=archiveId` and `volumeOrdinal=0` for one-file artifacts; multipart ordinals start at 1. Precompute counts/offsets/lengths/final flags from stable record length and verify them during import. Any disk-spooled inventory/footer state remains encrypted/authenticated under the archive key and is removed with the partial artifact on failure.

Probe local destination capacity, writability, path safety, and maximum single-file size before publication. Produce one `.anima` file through fsync/reopen/verify/parent-fsync/rename when supported. When a FAT32-like limit blocks the estimated file but total capacity is sufficient, write a same-destination `<set>.partial/`; write/fsync/verify/rename every `volume-####.anima-part.partial`, publish the authenticated `core.anima` controller last, fsync the directory, then atomically rename the directory. Reject destinations without same-filesystem atomic rename and reject missing, duplicated, reordered, mixed-set, truncated, appended, or corrupt volumes before import activation.

Import preflights capacity for a complete same-volume sibling staging Core plus margin while retaining any existing Core. It streams/authenticates into staging and activates a new destination by fsync + directory rename. Replacement never overwrites in place: lock, verify/fsync the new sibling, atomically swap the machine-local active-Core registry pointer, record completion, and retain the old Core for rollback. Startup recovers interrupted activation from the last authenticated registry generation. Inject failures at every staging/rename/pointer/completion boundary. V2 has no 16-MiB total content-section ceiling. Until Task 9, the encrypted Soul file may still contain read-only legacy rollback tables, but no active app service treats them as authority.

- [ ] **Step 8: Disable legacy authority without deleting recovery sources**

Progress (2026-08-13): authenticated forward-only task CRUD, portable-
preference patches, and presence preference GET/PUT now commit/read only
through native authenticated CoreFS authority. Background presence/initiative/
dream consent checks follow the same canonical object and fail closed without
an unlocked CoreFS session instead of consulting retained SQL. Account
identity/demographic updates, login hydration, onboarding completion, and
identity-override checks now use the authenticated account-profile object;
retained SQL account/setup rows are not changed and the unsafe directory-only
delete path fails closed pending restart-safe whole-Core deletion.
Task bodies and caller-bound opaque catalog IDs publish atomically, task API
IDs no longer depend on the legacy SQL allocator, delete targets a stable
`core.trash` recovery root, local session authority generations advance after
each trusted native result, and focused tests prove these routes do not touch
their retained legacy write paths. Restart-safe account deletion, conversation/
diary/asset/document writers, and the full raw Runtime/cache/log/index
plaintext scan remain open, so this step is intentionally not checked complete.
Canonical thread lifecycle is now also active: list/read, create/reuse,
reset/clear, close, and delete authenticate one bounded CoreFS snapshot and
commit only native optimistic mutations, including atomic close-plus-create
and thread-plus-segment trash. Retained Runtime thread/message rows remain
unchanged. Ordinary blocking and streaming agent turns now append visible
user/assistant bodies only as canonical CoreFS message events, rebuild prompt
history from that authority, and retain only null-body CoreFS references in
fresh Runtime rows. Approval checkpoints remain unlock-sealed operational
Runtime state, and resumed visible responses return only to CoreFS with
collision-free Runtime/step metadata. Visible message edit/delete, attachments,
and diary/asset/document writers remain open.

Assert app routes/services use CoreFS for migrated families and cannot write legacy Soul/runtime content tables. Scan the fresh active PostgreSQL and every instance-local cache/log/index path for seeded portable/message/chunk/OCR/source/candidate/pending-op plaintext; require zero hits. Verify sealed operational rows decrypt only while unlocked and that rebuildable plaintext exists only in process memory. Keep legacy SQLCipher tables/models read-only solely for rollback/recovery until Task 9's later cleanup release.

- [ ] **Step 9: Update architecture documentation**

Update `docs/thesis/whitepaper.md`, `docs/thesis/portable-core.md`, `docs/thesis/three-tier-architecture.md`, `docs/architecture/README.md`, `docs/architecture/memory/memory-system.md`, `docs/architecture/system/database-schema.md`, and `docs/prds/three-tier-architecture.md` to describe the refined Core/Soul/CoreFS/Runtime boundary. Reconcile every node and edge in `docs/architecture/system/anima-core-filesystem.md` against the implemented routes, crates, storage paths, permission matrix, startup states, and transfer modes; remove its planned-status warning only after the complete cutover acceptance gate passes.

- [ ] **Step 10: Run focused migration/transfer/authority tests**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_cutover.py apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_authority.py apps/server/tests/test_corefs_soul_relocation.py apps/server/tests/test_vault.py apps/server/tests/test_health_integration.py -q
bun test apps/desktop/tests/corefs-transfer.test.ts
cargo test -p anima-core capsule
cargo test -p anima-core core_archive
```

Then, under separate funded execution authority, enable/dispatch the protected
desktop package gate against the final signed MSI, notarized PKG, DEB, and RPM.
Require all four native jobs to pass and record the exact package digests before
Step 4 can publish the irreversible marker or Step 12 can publish a release.

- [ ] **Step 11: Run full validation**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test
bun run test:desktop
bun run lint
bun run build
bun run db:server:current
```

Then start the app, verify `GET /health`, and smoke-test unlock/auth, chat, thread history, diary/drafts, notes through CoreFS, gallery, document upload/reindex, tasks, settings, memory promotion/provenance, lock, restart, Runtime deletion/rebuild, a full single-file transfer, FAT32-like multipart transfer, scoped credential replacement, degraded Soul-only `filesystem_missing` recovery, CoreFS-only recovery/export mode plus V1 reattachment rejection, interrupted removable-media export, interrupted active-Core pointer swap, rollback to the retained old Core, and clean-environment restore.

- [ ] **Step 12: Commit first-release cutover**

```powershell
git add apps/server/src/anima_server/services/corefs apps/server/src/anima_server/schemas/corefs_transfer.py apps/server/src/anima_server/schemas/vault.py apps/server/src/anima_server/api/routes/corefs_transfer.py apps/server/src/anima_server/api/routes/vault.py apps/server/src/anima_server/db/session.py apps/server/src/anima_server/db/user_store.py apps/server/src/anima_server/services/storage.py apps/server/src/anima_server/services/vault.py apps/server/src/anima_server/services/anima_core_bindings.py apps/server/src/anima_server/main.py packages/anima-core/src/core_archive.rs packages/anima-core/src/capsule.rs packages/anima-core/src/integrity.rs packages/anima-core/src/ffi.rs packages/anima-core/src/lib.rs packages/api-client/src/client.ts packages/api-client/src/types.ts apps/desktop/src/pages/settings/CoreTransferSettings.tsx apps/desktop/src/pages/settings/VaultSettings.tsx apps/server/tests/test_corefs_cutover.py apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_authority.py apps/server/tests/test_corefs_soul_relocation.py apps/server/tests/test_vault.py apps/server/tests/test_health_integration.py apps/desktop/tests/corefs-transfer.test.ts docs
git -c commit.gpgsign=false commit -m "core: cut over to CoreFS authority"
```

## Task 9: Later-release Soul cleanup and legacy retirement

**Ticket:** `PCF-009`  
**Depends on:** `PCF-008`, one accepted stable-release observation window, explicit cleanup approval

**Files:**
- Create: `apps/server/alembic_core/versions/20260712_0002_retire_app_tables_from_soul.py`
- Create: `apps/server/src/anima_server/models/soul_owner.py`
- Modify: `apps/server/src/anima_server/db/session.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Delete: `apps/server/src/anima_server/models/user.py`
- Delete: `apps/server/src/anima_server/models/user_key.py`
- Modify: `apps/server/src/anima_server/models/agent_runtime.py`
- Modify: `apps/server/src/anima_server/models/consciousness.py`
- Modify: `apps/server/src/anima_server/models/soul_consciousness.py`
- Modify: `apps/server/src/anima_server/services/core.py`
- Modify: `apps/server/src/anima_server/services/auth.py`
- Modify: `apps/server/src/anima_server/services/recovery.py`
- Modify: `apps/server/src/anima_server/services/vault.py`
- Modify: `apps/server/src/anima_server/db/user_store.py`
- Modify: `apps/server/src/anima_server/services/agent/biography_preview.py`
- Modify: `apps/server/src/anima_server/services/agent/episodes.py`
- Modify: `apps/server/src/anima_server/services/agent/memory_blocks.py`
- Modify: `apps/server/src/anima_server/services/agent/embedding_contract.py`
- Modify: `apps/server/src/anima_server/services/agent/embeddings.py`
- Modify: `apps/server/src/anima_server/services/agent/agent_experience.py`
- Modify: `apps/server/src/anima_server/services/agent/knowledge_graph.py`
- Modify: `apps/server/src/anima_server/services/health/checks.py`
- Modify: `apps/server/src/anima_server/api/routes/eval.py`
- Modify: `apps/server/src/anima_server/api/routes/auth.py`
- Modify: `apps/server/src/anima_server/api/routes/users.py`
- Modify: `apps/server/src/anima_server/api/routes/db.py`
- Modify: `apps/server/src/anima_server/api/routes/ws.py`
- Modify: `apps/server/src/anima_server/api/routes/vault.py`
- Modify: `apps/server/src/anima_server/api/routes/telegram.py`
- Delete: `apps/server/src/anima_server/models/task.py`
- Delete: `apps/server/src/anima_server/models/presence.py`
- Delete: `apps/server/src/anima_server/models/links.py`
- Modify: `apps/server/src/anima_server/services/corefs/migration.py`
- Modify: architecture/schema/migration documentation
- Test: `apps/server/tests/test_corefs_soul_purity.py`
- Test: `apps/server/tests/test_corefs_legacy_retirement.py`
- Test: `apps/server/tests/test_embedding_contract.py`
- Test: `apps/server/tests/test_agent_experience.py`
- Test: `apps/server/tests/test_knowledge_graph.py`

- [ ] **Step 1: Enforce the later-release cleanup gate**

This is the **pre-apply authorization gate**. Require authenticated forward-only marker, completed encrypted legacy-runtime recovery bundle, successful Task 8 release observation, verified current backup, and explicit cleanup approval. Preflight exactly one legacy numeric owner row, one matching opaque Core owner UUID, complete account-profile migration, complete thread/message ID map, complete Soul row inventory, a deterministic provenance transformation plan, and zero unresolved migration conflicts. An unmarked, multi/ambiguous-owner, FK-inconsistent, or still rollback-capable Core must never reach the cleanup revision. Do not require post-cleanup checks before applying the cleanup.

- [ ] **Step 2: Write failing physical Soul-purity tests**

Inspect SQLCipher schema/model allowlists and prove diary, tasks, messages, app account/profile/auth, presence, and integration link data are absent after cleanup while every Soul memory/identity table remains. Capture pre-cleanup row counts/hashes for every retained table. Untouched retained tables require identical row counts and hashes. Intentionally transformed owner/provenance tables require unchanged row counts plus deterministic expected-transformation hashes computed from the preflight owner/thread/message/run mapping. All paths require `PRAGMA foreign_key_check` with zero rows.

- [ ] **Step 3: Add the gated cleanup Alembic target**

Modify `ensure_user_database()` to upgrade only through the compatibility revision unless the cleanup gate passes. Fresh CoreFS-format Cores use the clean head. Never run `20260712_0002` on an unmarked legacy Core.

- [ ] **Step 4: Replace the application User row with an internal Soul owner anchor**

Create `soul_owners(id INTEGER PRIMARY KEY, core_owner_id TEXT UNIQUE NOT NULL, created_at, updated_at)`. Preserve the legacy numeric `users.id` as `soul_owners.id` in V1 so every retained Soul row and service keeps its compatibility integer; store the manifest's opaque owner UUID in `core_owner_id`. Remove username/password/display/demographic fields now stored in the encrypted account-profile object. Runtime uses the same numeric compatibility ID plus Core UUID without a Soul FK.

Use SQLite/Alembic `batch_alter_table` rebuilds to retarget `user_id` FKs from `users.id` to `soul_owners.id` for these retained tables: `identity_blocks`, `growth_log`, `core_emotional_patterns`, `self_model_blocks`, `agent_profile`, `emotional_signals`, `memory_items`, `memory_episodes`, `foresight_signals`, `agent_experiences`, `agent_skills`, `memory_item_tags`, `memory_item_evidence`, `memory_claims`, `memory_claim_evidence`, `user_profile_fields`, `user_profile_field_evidence`, `forget_audit_log`, `kg_entities`, and `kg_relations`. For tables without a direct `user_id` FK, verify their parent FK chain instead of inventing a column.

- [ ] **Step 5: Remove converted app tables and dormant models**

Before dropping `agent_threads`, convert every retained provenance field that points at legacy thread/message/run IDs. At minimum migrate `memory_episodes.thread_id`/`transcript_ref`, `emotional_signals.thread_id`, `foresight_signals.source_thread_id`/`source_message_ids_json`, and `agent_experiences.source_thread_id`/`source_run_id` to verified Core thread/message URIs or explicitly nullable audited provenance. Require mapped counts/hashes and no orphan reference before the source FK/table disappears.

Then drop diary/folder/attachment, task, legacy thread/message/run/step, presence/config/link, app account/auth (`users`, `user_keys`), `memory_vectors`, `experience_cluster_state` (its centroid vectors are a rebuildable index), legacy Soul `background_task_runs`, and other obsolete/derived tables identified by the checked inventory. Rebuild retained tables to remove every derived vector/cache column: `memory_items.embedding_json`/`embedding_checksum`, `agent_experiences.embedding_json`, `agent_skills.embedding_json`, and `kg_entities.embedding_json`/`embedding_checksum`. Also remove mixed-semantics operational/app fields already migrated in Task 7: `agent_profile.setup_complete`, `self_model_blocks.needs_regeneration`, and `memory_episodes.needs_regeneration`; onboarding comes from the portable account/preferences object and regeneration work comes from Runtime. Update embedding, experience-clustering, graph, health, eval, and vault code so vectors are generated into unlock-scoped process memory only and are rebuilt from retained Soul text/state after restart or Runtime deletion. Delete obsolete ORM exports rather than leaving classes or fields mapped to missing schema. Retain the meaningful memories, claims/evidence, episodes, procedural experiences and distilled skills, self-model, emotional/growth state, learned user/relationship model, enduring intentions, consolidated graph, `soul_owners`, and `soul_keyslots`.

Before deleting `models/user.py` and `models/user_key.py`, migrate every remaining consumer. `services/agent/biography_preview.py`, `episodes.py`, and `memory_blocks.py` read private profile fields from the Core account-profile service; `api/routes/db.py` and `ws.py` validate `SoulOwner`; `api/routes/vault.py` and `services/vault.py` export/import the account-profile object and manifest/Soul keyslots; `services/auth.py`, `recovery.py`, `db/user_store.py`, `api/routes/auth.py`, `users.py`, and `telegram.py` use the new unlock/account/integration boundaries. A repository `rg` for `User`, `UserKey`, `models.user`, `users.username`, and `user_keys` must have no active-code hits except migration fixtures/docs before table drop.

- [ ] **Step 6: Vacuum while retaining recovery artifacts**

Run SQLCipher `VACUUM` after verified drops, but retain the verified encrypted legacy PostgreSQL bundle and pre-cleanup backup until post-apply validation passes.

- [ ] **Step 7: Run cleanup-focused and full validation**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_soul_purity.py apps/server/tests/test_corefs_legacy_retirement.py apps/server/tests/test_corefs_transfer.py apps/server/tests/test_recovery.py apps/server/tests/test_embedding_contract.py apps/server/tests/test_agent_experience.py apps/server/tests/test_knowledge_graph.py -q
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; bun run test
bun run test:desktop
bun run lint
bun run build
bun run db:server:current
```

This is the **post-apply release gate**. After commands pass, close/reopen with both passphrase and recovery phrase, run `PRAGMA foreign_key_check`, compare untouched-table hashes and transformed-table expected hashes, verify schema/model allowlists, delete/rebuild Runtime, and transfer/unlock the cleaned Core in a fresh environment. Failure restores the verified pre-cleanup backup; it does not authorize deleting recovery artifacts.

- [ ] **Step 8: Finalize irreversible legacy retirement**

Only after Step 7 passes, record `soul_cleanup_complete` with verification hashes in the manifest/health state. Delete the encrypted legacy PostgreSQL recovery bundle and converted source backups according to the approved retention policy.

- [ ] **Step 9: Commit the later-release cleanup separately**

```powershell
git add apps/server/alembic_core/versions/20260712_0002_retire_app_tables_from_soul.py apps/server/src/anima_server/db/session.py apps/server/src/anima_server/models apps/server/src/anima_server/services/core.py apps/server/src/anima_server/services/auth.py apps/server/src/anima_server/services/recovery.py apps/server/src/anima_server/services/vault.py apps/server/src/anima_server/db/user_store.py apps/server/src/anima_server/services/agent/biography_preview.py apps/server/src/anima_server/services/agent/episodes.py apps/server/src/anima_server/services/agent/memory_blocks.py apps/server/src/anima_server/services/agent/embedding_contract.py apps/server/src/anima_server/services/agent/embeddings.py apps/server/src/anima_server/services/agent/agent_experience.py apps/server/src/anima_server/services/agent/knowledge_graph.py apps/server/src/anima_server/services/health/checks.py apps/server/src/anima_server/api/routes/eval.py apps/server/src/anima_server/api/routes/auth.py apps/server/src/anima_server/api/routes/users.py apps/server/src/anima_server/api/routes/db.py apps/server/src/anima_server/api/routes/ws.py apps/server/src/anima_server/api/routes/vault.py apps/server/src/anima_server/api/routes/telegram.py apps/server/src/anima_server/services/corefs/migration.py apps/server/tests/test_corefs_soul_purity.py apps/server/tests/test_corefs_legacy_retirement.py apps/server/tests/test_embedding_contract.py apps/server/tests/test_agent_experience.py apps/server/tests/test_knowledge_graph.py docs
git -c commit.gpgsign=false commit -m "core: retire legacy app data from the Soul"
```

## Task 10: Retained-catalog garbage collection and cryptographic key retirement

**Ticket:** `PCF-010`  
**Depends on:** `PCF-008`, an accepted retention policy, verified current backup, and no active transfer/rotation/migration

**Files:**
- Create: `packages/anima-corefs/src/gc.rs`
- Modify: `packages/anima-corefs/src/catalog/`
- Modify: `packages/anima-corefs/src/trash.rs`
- Modify: `packages/anima-core/src/ffi.rs`
- Create: `apps/server/src/anima_server/services/corefs/gc.py`
- Create: `apps/server/src/anima_server/schemas/corefs_maintenance.py`
- Create: `apps/server/src/anima_server/api/routes/corefs_maintenance.py`
- Modify: `apps/server/src/anima_server/services/corefs/keyslots.py`
- Modify: `apps/server/src/anima_server/services/corefs/migration.py`
- Modify: `apps/server/src/anima_server/services/vault.py`
- Modify: `apps/server/src/anima_server/main.py`
- Modify: `packages/api-client/src/client.ts`
- Modify: `packages/api-client/src/types.ts`
- Modify: `apps/desktop/src/pages/settings/SecuritySettings.tsx`
- Test: `apps/server/tests/test_corefs_gc.py`
- Test: `apps/server/tests/test_corefs_key_retirement.py`
- Test: `apps/desktop/tests/corefs-maintenance.test.ts`

- [ ] **Step 1: Define and expose the retention policy**

Keep `fs/HEAD`, every catalog/object generation inside the configured restore window, any generation pinned by an in-progress transfer/rotation/migration, and at least one last-known-good predecessor. The default restore window is 30 days; changing it or requesting cryptographic deletion requires explicit authenticated confirmation. An authoritative dry-run requires an unlocked Core so it can authenticate retained catalogs; it lists reachable catalogs/objects, prunable orphan/revision bytes, required FRK/Object-DEK versions, backup blockers, and capacity impact without returning private logical names. While locked, expose only cached non-private last-run status or `unlock_required`, never a fresh reachability claim.

- [ ] **Step 2: Write failing mark/sweep and failure-recovery tests**

Cover reachability across retained catalogs, tombstones, interrupted object publication, concurrent writer exclusion, corrupt catalogs, symlink/junction/path attacks, crash after mark/before sweep, crash mid-sweep, idempotent resume, transfer/rotation pins, backup pins, and proof that the only copy of a reachable object/catalog/key is never removed. Add `purge` authorization tests for live-object rejection, trash/revision/inventory preconditions, recent user reauthentication, one-use bound confirmation, ANIMA/client rejection even with `manage`, stale/replayed confirmation, recursive folder contents, known-backup warnings, and zero local wrapped-DEK references before retirement.

- [ ] **Step 3: Implement authenticated prune/doctor**

Acquire the same OS-backed Core-wide maintenance lock used by catalog commits. Authenticate every retained catalog before computing the union of reachable physical object revisions and required key versions. Persist only a safe resumable prune journal outside the Core, rename candidates into an internal quarantine generation before final deletion, fsync each phase, and re-read `fs/HEAD` before every destructive batch. A changed `fs/HEAD` aborts and recomputes; corruption blocks deletion rather than guessing reachability.

- [ ] **Step 4: Implement the explicit user-only purge and cryptographic deletion semantics**

Implement `purge(trash_id, expected_trash_revision, confirmation)` only in the authenticated maintenance service. Require a fresh passphrase/recovery reauthentication and a one-use challenge bound to Core/trash/revision/recursive inventory/restore-window waiver/known-backup warnings. Reject live targets, stale trash, ANIMA/client callers, active operation pins, and confirmation replay. Prune all explicitly waived local catalogs/revisions wrapping each target Object DEK, prove zero local wrapped references, then retire that object key; remove no shared FRK merely to delete one object. Explain that local purge cannot erase SSD wear-leveling, exported archives, or old backups and report every known copy that may still contain it.

- [ ] **Step 5: Retire superseded Object DEKs and decrypt-only FRKs**

An Object DEK has no password/recovery keyslot: it retires only when authenticated pruning removes every retained catalog/revision containing its wrapped copy and reachability proves zero remaining references. For a decrypt-only FRK, additionally require no retained local catalog/object wrapper needs that FRK version, no active operation pins it, passphrase and recovery reopen under the active generation pass, and at least one verified backup uses the active generation. Only then atomically remove that FRK version's matching password/recovery manifest keyslots, reopen/verify again, and retain the pre-retirement backup until post-check completion. Never silently retire either kind of key because a time threshold elapsed.

- [ ] **Step 6: Add maintenance API/UI**

Expose authenticated dry-run, prune, resume, cancel-before-sweep, user-only trash purge, and key-retirement operations with phase/progress/blockers. Security settings shows restore-window consequences, bound recursive inventory, backup warnings, active/decrypt-only key versions, reclaimable bytes, and an explicit irreversible confirmation after reauthentication. Do not expose raw object filenames or keys.

- [ ] **Step 7: Validate and commit separately**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run pytest apps/server/tests/test_corefs_gc.py apps/server/tests/test_corefs_key_retirement.py apps/server/tests/test_corefs_rotation.py apps/server/tests/test_corefs_transfer.py -q
cargo test -p anima-corefs -p anima-core
bun test apps/desktop/tests/corefs-maintenance.test.ts
bun run --cwd apps/desktop build
```

Then run the full suite, prepare a transfer, prune an orphan/revision fixture, reopen through both passphrase and recovery paths, restore the verified active-generation backup, and confirm a deleted Runtime still rebuilds from the pruned Core.

```powershell
git add packages/anima-corefs/src/gc.rs packages/anima-corefs/src/catalog packages/anima-corefs/src/trash.rs packages/anima-core/src/ffi.rs apps/server/src/anima_server/services/corefs/gc.py apps/server/src/anima_server/services/corefs/keyslots.py apps/server/src/anima_server/services/corefs/migration.py apps/server/src/anima_server/services/vault.py apps/server/src/anima_server/schemas/corefs_maintenance.py apps/server/src/anima_server/api/routes/corefs_maintenance.py apps/server/src/anima_server/main.py packages/api-client/src/client.ts packages/api-client/src/types.ts apps/desktop/src/pages/settings/SecuritySettings.tsx apps/server/tests/test_corefs_gc.py apps/server/tests/test_corefs_key_retirement.py apps/desktop/tests/corefs-maintenance.test.ts
git -c commit.gpgsign=false commit -m "core: prune retained revisions and retire old keys"
```

## Dependency Graph

```text
PCF-001 key hierarchy
  -> PCF-002 shared file tools/object/catalog/CoreFS
      -> PCF-003 runtime/indexing
          -> PCF-004 diary/notes
          -> PCF-005 conversations
               -> PCF-006 assets/documents
                    -> PCF-007 account/tasks/preferences
                         -> PCF-008 cutover/transfer
                              -> PCF-009 later-release Soul cleanup
                              -> PCF-010 later maintenance/GC and key retirement
```

## Release Gates

No default cutover until all are true:

- password and recovery unlock every root and Soul-domain keyslot
- catalog benchmark meets the fixed reference-profile targets or the catalog design is revised
- raw Core/runtime scans reveal no seeded private plaintext
- runtime deletion/rebuild loses no canonical data
- diary, conversation, gallery, document, task, account, and preference converters are idempotent and verified
- first-write forward-only marker and crash recovery pass
- cold and live prepared transfers work in a clean environment
- full backend/desktop build, lint, test, health, and smoke suite pass
- final signed MSI, notarized PKG, DEB, and RPM replacement-install evidence
  passes the protected draft-cleanup authority workflow, with exact artifact
  digests recorded before irreversible cutover or release publication

Task 9 has two distinct gates:

- **Pre-apply authorization:** Task 8 gates remain green through the approved observation window; verified current/pre-cleanup backups exist; explicit cleanup approval is recorded; one-to-one numeric/opaque owner mapping, complete provenance transformation plan, retained-table baseline counts/hashes, and zero pre-existing FK/migration conflicts are proven.
- **Post-apply release/retirement:** untouched-table hash parity, transformed-table expected hashes, zero `PRAGMA foreign_key_check` rows, schema/model allowlist success, passphrase/recovery reopen, Runtime rebuild, and clean-environment transfer all pass. Only this second gate permits deleting encrypted legacy recovery artifacts and declaring cleanup complete.

Task 10 is the only slice authorized to prune retained catalog/object revisions or remove decrypt-only FRK/Object-DEK key material. Its dry-run, retention, verified-backup, active-operation, and post-reopen gates must all pass independently of Soul cleanup.

## Execution Handoff

Execute one `PCF-*` child ticket at a time in dependency order. Each ticket should normally be one reviewable PR. `PCF-009` and `PCF-010` are intentionally later releases and must never be combined with the first cutover PR.
