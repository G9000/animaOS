---
title: Portable Core Filesystem (Product)
author: Julio Caesar
last_edited: 2026-07-13
version: 1
status: approved
---

# Portable Core Filesystem v1

| Field | Value |
|---|---|
| Author | Julio Caesar |
| Version | 1 |
| Status | Approved |
| Created | 2026-07-12 |
| Last edited | 2026-07-13 |

> Keep the complete portable identity and user-owned experience in one Core while reserving SQLCipher exclusively for ANIMA's internal continuity and excluding device capabilities and external credentials.

## Context

The current three-tier architecture correctly separates durable Soul state from operational Runtime state, but its physical boundaries have drifted:

- SQLCipher contains application-owned records such as diary entries and tasks.
- PostgreSQL is stored under `.anima/` even though Runtime is defined as disposable.
- active threads and messages are authoritative in PostgreSQL until archived.
- gallery, diary, notes, settings, and conversation history do not share one generic Core Filesystem model.
- ANIMA accesses most application content through feature-specific database services rather than through a general filesystem interface.

This creates two conceptual problems. First, moving application records into SQLCipher makes the Soul database describe the product instead of the agent. Second, a copied Core is not independently authoritative while user-visible history remains in a machine-local runtime database.

## Product Definition

**animaOS** is the product. **ANIMA CORE** is its portable encrypted subsystem and transfer-artifact family. ANIMA CORE contains two independently recoverable compartments: the **Soul** for agent identity and cognitive continuity, and the **Core Filesystem** for portable user-owned application content. Device capabilities, local Runtime configuration, and external credentials are deliberately outside the portability promise.

The complete ANIMA CORE is the normal backup and transfer unit. Advanced recovery may export or restore either compartment independently without redefining one as the other:

- `anima-core-<timestamp>.anima` contains one coherent Soul/CoreFS generation pair.
- `anima-core-soul-<timestamp>.anima` contains only the Soul and its required unlock/recovery metadata.
- `anima-core-fs-<timestamp>.anima` contains only CoreFS catalogs, reachable objects, the FRK keyslots required to open them, and non-private recovery metadata.

Losing CoreFS is serious user-data loss, but it must not make a valid Soul cryptographically unusable. Losing the Soul means the files may be recoverable, but they do not constitute a complete ANIMA identity.

The placement rule is:

> If deleting data changes who ANIMA is, it belongs in the Soul. If it removes user-authored content or application state without changing ANIMA's identity, it belongs in the Core Filesystem. If it can be recomputed, retried, or discarded, it belongs in Runtime.

## What This Version Delivers

- SQLCipher contains only ANIMA's durable internal identity, memory, self-model, emotional development, learned user model, relationship model, growth history, and enduring agent intentions.
- Diary, notes, threads, messages, gallery assets, attachments, user-created tasks, and portable application preferences become encrypted canonical files inside `.anima/`.
- Local account profile fields become encrypted portable files; manifest keyslots hold only opaque owner IDs, KDF parameters, and wrapped SQLCipher Soul and Filesystem Root keys needed before unlock.
- ANIMA receives coding-agent-style CoreFS tools for bounded listing, walking, globbing, grepping, searching, reading, creating, writing, patching, moving, trashing, and restoring portable files.
- CoreFS is a user-customizable logical folder tree. Users, ANIMA, and approved clients can create and reorganize folders without breaking app features because app roots are resolved by stable folder roles rather than fixed paths.
- Folder policy distinguishes `user`, `anima`, and `shared` ownership from ANIMA access (`none`, `read`, `write`, or `manage`). Explicit deny wins, and ANIMA cannot grant itself more access to user-owned folders.
- A dedicated Rust `anima-file-tools` library supplies the bounded walk, glob, grep, read, and patch machinery shared by Animus host-file tools and CoreFS tools. The two backends remain explicit and never auto-route based on a path string.
- The desktop and the agent use the same CoreFS service so they cannot create competing sources of truth.
- Embedded PostgreSQL moves outside `.anima/` and becomes a machine-local, disposable runtime and indexing service associated with a stable Core ID.
- A moved or newly restored Core becomes usable through progressive startup indexing: a minimal catalog first, deeper text and semantic indexes in the background.
- Existing SQLCipher, PostgreSQL, transcript, diary attachment, and gallery data migrate with copy-verify-cutover behavior and rollback until the first accepted CoreFS mutation commits.
- Local export/import uses one versioned, streaming, authenticated ANIMA CORE format with `full`, `soul`, and `fs` payload kinds; it does not create three incompatible archive implementations.

## What Users See

- Cold-copying a closed `.anima/`, or choosing **Export ANIMA CORE**, carries ANIMA's Soul and all portable user content coherently.
- A normal export produces `anima-core-<timestamp>.anima`. Soul-only and CoreFS-only exports appear under Advanced Recovery rather than the primary transfer flow.
- Export streams bounded authenticated chunks directly to a local hard drive or removable drive, reports required/available capacity, and leaves only a clearly invalid `.partial` result if interrupted.
- Import preflights same-volume staging capacity, streams into a staging Core, verifies the declared inventory and compartment generations, and activates it only after complete authentication; it never partially overwrites the active Core and retains the previous Core for rollback when replacing one.
- Unlocking a moved Core shows its navigation and recent content as soon as the minimal catalog is ready.
- Search quality and semantic recall may improve progressively while background indexing continues.
- Indexing exposes progress, current phase, degraded capabilities, and retry state instead of blocking indefinitely on a generic loading screen.
- Restarting on the same machine reuses valid safe catalog/blind-token generations and only reconciles changed files; decrypted text and semantic indexes rebuild progressively after unlock.
- A missing or damaged runtime database does not remove diary entries, notes, gallery items, threads, or messages.
- Core content is readable only after ANIMA unlocks it.
- After unlock, CoreFS behaves like a normal folder tree within its authorization boundary: users and approved clients may add folders, apps may bind features to stable folder roles, and ANIMA may read or mutate only at the granted access level.
- Normal deletion moves content into recoverable encrypted trash. Permanent purge and cryptographic deletion remain explicit user-authorized operations.
- Pre-unlock UI uses neutral branding, operating-system locale/accessibility settings, and opaque owner identity; private profile/avatar data appears only after content unlock.

## Rules and Constraints

- Files in CoreFS are canonical; PostgreSQL rows derived from them are never canonical.
- PostgreSQL data directories, caches, logs, temporary files, and machine-specific settings must not live under `.anima/`.
- No user-private canonical content may be stored as plaintext on disk.
- The CoreFS API must reject path traversal, absolute paths, symlink escape, stale-revision overwrites, and unsupported object formats.
- Every logical folder is a first-class catalog entry with a stable ID, including empty folders. Names and paths are presentation state; stable folder roles keep app bindings intact across rename and move.
- Reserved animaOS folder roles and client-namespaced roles are unique. V1 reserves `core.journal`, `core.notes`, `core.conversations`, and `core.gallery`; each resolves to one stable folder ID regardless of rename or move. A client may extend only folders and metadata covered by an explicit user-approved, folder-scoped capability and may never claim a reserved role or change its own grant.
- Client-authored folders/metadata are portable, but executable client grants are device-local and bound to the installed client identity. Moving a Core requires explicit approval again on the destination machine.
- Folder policy is inherited unless explicitly overridden; an explicit deny takes precedence over inherited or direct allow. ANIMA cannot self-elevate a user-owned folder, and client principals cannot alter grants.
- Host filesystem tools and CoreFS tools have visibly different names, authorization, URI schemes, and backend handles. No path heuristic may silently cross the boundary.
- All file mutations must be atomic and crash-safe.
- A multi-file CoreFS patch must preflight every path, permission, revision, and format before publication, then commit one catalog generation or no generation. Shared parsing does not weaken this guarantee on backends that cannot provide transactions.
- A successful canonical file write must not be rolled back because indexing failed; indexing becomes stale and retries.
- Startup must provide a bounded path to catalog readiness and must not require semantic embedding completion before the app becomes usable.
- V1 uses full immutable catalog snapshots and is benchmarked for up to 25,000 live catalog entries, including first-class folders, and a 16 MiB serialized catalog; larger incremental catalog structures are deferred.
- Indexing must be resumable and idempotent after shutdown, crash, cancellation, or transfer.
- A Core moved between machines must not depend on copied PostgreSQL state.
- A raw recursive copy while the server is actively writing is unsupported; live transfer must use a short write barrier and verified snapshot.
- A complete snapshot records a coherent `(soulGeneration, filesystemGeneration)` pair, but Soul and CoreFS use separate keys and can be verified and recovered independently.
- Soul must not contain hard database foreign keys into CoreFS. Provenance and cross-compartment references use stable `corefs://` URIs plus authenticated content/revision hashes and tolerate an explicit `filesystem_missing` state.
- A Soul-only restore may run identity/cognition in explicit `filesystem_missing` degraded mode and may initialize a new empty CoreFS only after explicit confirmation plus complete new FRK password/recovery wrapping. A CoreFS-only restore opens through animaOS in restricted authenticated recovery/export mode and cannot start the agent as a complete ANIMA.
- Local export preflights destination free space, writable status, path safety, and maximum single-file size. Large destinations use one `.anima` file; a destination such as FAT32 that cannot hold it uses an authenticated bounded-volume set or is rejected before writing.
- Soul writes remain restricted to the existing consolidation/Soul Writer boundary.
- User content can inform Soul memory only through an explicit memory-candidate and consolidation path with source provenance.
- Device-specific preferences and secrets remain outside the portable Core. Secrets use the operating-system credential store.

## Canonical Content Families

| Family | Logical representation after unlock | Examples |
|---|---|---|
| Diary | sanitized HTML with structured metadata | rich entries, folders, covers, extracted inline media, attachments |
| Folders | first-class catalog entries with stable ID, parent, role, owner, access policy, and namespaced metadata | user folders, ANIMA folders, shared folders, stable/empty app roots |
| Notes | Markdown or HTML with structured metadata | user notes, linked documents |
| Conversations | thread metadata plus append-oriented JSONL message records | threads, messages, attachment references |
| Gallery | encrypted media objects plus structured metadata | images, captions, provenance |
| Tasks | structured JSON documents | user-created tasks and completion state |
| Preferences | structured JSON documents | portable appearance and interaction preferences |
| Attachments | encrypted binary objects with hashes and metadata | documents, audio, video, arbitrary files |

Physical filenames may be opaque. Logical paths and familiar formats are exposed through the unlocked CoreFS service. V1 is a virtual, unmounted filesystem; a future version may add an authenticated OS mount adapter without changing the canonical format.

## Startup and Transfer Experience

Startup after unlock has explicit readiness stages:

1. **Core open**: validate manifest, Core ID, schema versions, and encryption envelopes.
2. **Catalog reconcile**: follow `fs/HEAD`, open the committed encrypted catalog generation, validate only its live object mappings, quarantine isolated bad objects, and populate enough metadata for navigation. Unreferenced physical objects are never resurrected during normal startup.
3. **Content index**: build the unlock-scoped searchable text index and refresh safe blind tokens.
4. **Semantic index**: generate unlock-scoped in-memory embeddings and retrieval structures.
5. **Ready**: all requested index families are current; failed optional families remain visible and retryable.

The desktop becomes usable at catalog readiness. Isolated corrupt or unsupported objects produce an explicit degraded-ready state while unaffected content remains available. Text and semantic search can report partial/degraded status while later stages continue. In V1, decrypted full-text, chunks/OCR/source spans, and semantic indexes exist only in process memory while the Core is unlocked; PostgreSQL persists safe catalog/checkpoint/operational fields and, only where crash durability is necessary, authenticated operational ciphertext under an unlock-derived non-persisted sealing key. It never persists plaintext CoreFS bodies, previews, chunks, OCR, source spans, or embeddings.

Local transfer is streamed rather than assembled in memory. Export pins one committed CoreFS catalog generation, checkpoints a bounded Soul snapshot, then reads at most 8 MiB of source data at a time, authenticates/encrypts it at the archive layer, and writes it to `<final-name>.partial`. Completion writes and verifies the authenticated footer before an atomic rename. The resulting size is approximately the encrypted Soul plus reachable encrypted CoreFS bytes and small framing overhead; encrypted media is not expected to compress materially. Import performs the reverse bounded stream into a staging directory. ANIMA CORE archives are for transfer and recovery; the live Core is not run directly from removable media.

## Success Metrics

| Metric | Target | How to measure |
|---|---|---|
| Transfer completeness | 100% of supported CoreFS files are available after copying only `.anima/` | Seed all file families, copy to a clean machine fixture, unlock, index, and compare canonical inventories and hashes |
| Compartment recovery | A valid Soul-only artifact restores identity into `filesystem_missing`, and a valid CoreFS-only artifact opens in recovery/export mode without impersonating a complete ANIMA | Round-trip all three payload kinds and assert allowed/forbidden startup transitions |
| Removable-drive streaming | Export/import memory remains bounded for archives larger than RAM; insufficient capacity and single-file limits fail before canonical publication | Stream a large binary fixture to exFAT-like and FAT32-like destination adapters with chunk/multipart/interruption assertions |
| Atomic restore | Every failed import leaves the previous active-Core registry generation and Core directory unchanged | Inject failure at staging, verification, rename, registry-swap, and completion boundaries |
| Soul purity | No app-specific canonical tables or feature CRUD paths remain in active SQLCipher schema/services after cutover | Schema allowlist test plus dependency/import boundary test |
| Runtime disposability | Deleting the runtime directory loses no canonical user content or Soul state | Delete runtime fixture, restart, rebuild, and compare user-visible content |
| Catalog readiness | Existing medium-size Core reaches navigable state without waiting for embeddings | Startup benchmark with staged readiness assertions |
| Catalog scale | Full-snapshot catalog commit meets p95 <= 100 ms with 5,000 live entries plus 500 tombstones and p95 <= 250 ms both with 25,000 live entries plus 2,500 tombstones within the 16-MiB envelope and at the 16-MiB serialized-catalog limit | Deterministic release-build catalog publication benchmark using the fixture matrix and hardware/storage profile in the design, with fsync enabled and live/tombstone/total counts recorded; a maximum-live fixture above 16 MiB fails the support envelope rather than benchmarking an unsupported catalog |
| Progressive indexing | Interrupted indexing resumes or safely restarts without duplicate or missing index rows | Failure injection at each phase and repeated reconciliation |
| Atomic content safety | No acknowledged Core write produces a partial or undecryptable canonical object | Crash/failure injection around temporary write, fsync, rename, and index notification |
| Locked privacy | Canonical Core files reveal no private content through direct disk inspection | Seed known plaintext markers and scan raw Core bytes before unlock |
| Tool parity | ANIMA can use bounded list, walk, glob, grep, search, read, create, write, patch, move, trash, and restore operations on every supported content family | Shared `anima-file-tools` contract tests plus HostFS and CoreFS backend suites |
| Stable app folders | Renaming or moving a role-bound folder does not break Journal, Notes, Conversations, Gallery, or an approved client extension | Bind by stable folder ID/role, rename/move, restart, and assert identical feature resolution |
| Least privilege | ANIMA and clients cannot exceed inherited folder policy or elevate their own grants | Deny-precedence, owner-control, client-capability, lock-revocation, and adversarial cross-backend tests |

## Out of Scope

- Cloud upload, cloud backup, cloud synchronization, remote replication, or collaborative multi-writer editing. V1 defines local storage, local backup/export, and machine-to-machine transfer only.
- An OS-level CoreFS mount or direct external-editor integration in V1. These may be added later through an authenticated adapter after the virtual filesystem is stable.
- Attaching a CoreFS-only recovery artifact to a Soul in V1; V1 supports authenticated browse/export only until lineage, conflict, and atomic-pair semantics receive a separate design.
- Treating PostgreSQL indexes as portable backup artifacts.
- A general-purpose shell with unrestricted access to the host filesystem.
- Automatically treating a CoreFS path as a host path, or a host path as CoreFS.
- Storing provider keys or connector tokens inside CoreFS files.
- Replacing the existing Soul memory/consolidation semantics.
- Converting every imported binary document into Markdown.

## Relationship to Existing Architecture

This PRD refines and partially supersedes the storage-placement decisions in [Three-Tier Cognitive Architecture](three-tier-architecture.md):

- Soul remains SQLCipher and the consolidation boundary remains intact.
- Runtime remains PostgreSQL, but its physical data directory moves outside `.anima/`.
- canonical conversations move from Runtime/archive split ownership into the encrypted Core Filesystem.
- Archive becomes a content format/lifecycle state inside the Core Filesystem rather than the only durable home for messages.

## References

- [Portable Core thesis](../thesis/portable-core.md)
- [Three-tier architecture thesis](../thesis/three-tier-architecture.md)
- [Architecture overview](../architecture/README.md)
- [Memory system](../architecture/memory/memory-system.md)
- [Encrypted Core v1](crypto/encrypted-core-v1.md)
- [Portable Core Filesystem design](../superpowers/specs/2026-07-12-portable-core-filesystem-design.md)
- [Portable Core key hierarchy and rotation](../superpowers/specs/2026-07-12-portable-core-key-hierarchy-design.md)
- [ANIMA CORE Filesystem target architecture](../architecture/system/anima-core-filesystem.md)
