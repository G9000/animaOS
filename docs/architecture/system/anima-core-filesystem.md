---
title: ANIMA CORE Filesystem Target Architecture
description: End-to-end target architecture for Soul, CoreFS, Runtime, tools, permissions, indexing, and local transfer
category: architecture
status: planned
last_edited: 2026-07-12
---

# ANIMA CORE Filesystem Target Architecture

> **Status: planned target architecture.** These diagrams describe the approved Portable Core Filesystem design and implementation sequence. They do not claim that the migration is already implemented.

**Normative sources:** [Portable Core Filesystem design](../../superpowers/specs/2026-07-12-portable-core-filesystem-design.md), [key hierarchy design](../../superpowers/specs/2026-07-12-portable-core-key-hierarchy-design.md), and [implementation plan](../../superpowers/plans/2026-07-12-portable-core-filesystem.md).

## 1. Complete System Topology

animaOS is the product. ANIMA CORE is the portable encrypted Soul-plus-CoreFS subsystem. Runtime PostgreSQL, installed-client grants, device configuration, and external credentials remain machine-local.

```mermaid
flowchart TB
    subgraph Product["animaOS product surfaces"]
        Desktop["Desktop app<br/>Tauri + React"]
        Agent["ANIMA agent runtime"]
        Animus["Animus coding CLI"]
        Mod["Approved client or mod"]
    end

    subgraph Python["Python product and orchestration layer"]
        API["FastAPI routes<br/>auth, domain APIs, CoreFS API"]
        Domains["Domain services<br/>Journal, Notes, Threads, Gallery, Tasks"]
        CoreFacade["CoreFS Python facade<br/>schemas, migration, authority gate"]
        Indexer["Progressive indexer<br/>catalog, text, semantic readiness"]
        SoulWriter["Consolidation and Soul Writer"]
        Broker["Device-local capability broker"]
    end

    subgraph Rust["Rust storage and file-operation layer"]
        PyO3["Existing anima-core PyO3 extension"]
        CoreEngine["anima-corefs<br/>crypto, catalog, policy, transactions, trash"]
        FileTools["anima-file-tools<br/>walk, glob, grep, bounded read, patch plan"]
        HostBackend["Explicit HostFS backend"]
        CoreBackend["Explicit CoreFS backend"]
    end

    subgraph Portable["ANIMA CORE - portable .anima directory"]
        Manifest["manifest.json<br/>Core ID, opaque owner, wrapped root keyslots"]
        Soul["soul/soul.db<br/>SQLCipher agent identity and memory only"]
        Head["fs/HEAD<br/>authoritative catalog generation pointer"]
        Cutover["fs/CUTOVER_RECEIPT + CUTOVER_COMPLETE<br/>post-HEAD first-cutover recovery markers"]
        Catalog["Encrypted immutable catalogs<br/>folders, roles, policy, object references"]
        Objects["Encrypted immutable objects<br/>Markdown, JSON, JSONL, HTML, binary"]
    end

    subgraph Local["Machine-local and rebuildable state"]
        Runtime["Embedded PostgreSQL<br/>runs, jobs, checkpoints, safe indexes"]
        PlainIndex["Unlock-scoped memory<br/>plaintext search and embeddings"]
        Grants["Authenticated client grants<br/>Core, instance, package, folder, scope"]
        Credentials["OS credential store<br/>provider, connector, daemon secrets"]
        Device["Platform app data<br/>runtime path and device configuration"]
    end

    subgraph Host["Host filesystem outside ANIMA CORE"]
        Repo["User-authorized code folders"]
    end

    Desktop --> API
    Mod -->|"install or launch request"| Broker
    Broker -->|"short-lived scoped capability"| Mod
    Mod -->|"API call with capability"| API
    Animus --> FileTools

    API --> Domains
    Agent --> Domains
    Domains --> CoreFacade
    Agent -->|"corefs_* tools"| CoreFacade
    CoreFacade --> PyO3
    PyO3 --> CoreEngine
    CoreEngine -->|"uses bounded operations"| FileTools
    FileTools -->|"explicit CoreFS backend"| CoreBackend
    FileTools -->|"explicit HostFS backend"| HostBackend
    HostBackend --> Repo

    Manifest -->|"contains wrapped SQLCipher keyslot for"| Soul
    Manifest -->|"contains wrapped Filesystem Root Keyslot for"| CoreEngine
    CoreBackend --> Head
    CoreBackend -.->|"first cutover after HEAD"| Cutover
    Cutover -.->|"binds exact marked"| Head
    Head --> Catalog
    Catalog --> Objects

    Domains --> Indexer
    Indexer -->|"safe generations and checkpoints"| Runtime
    Indexer --> PlainIndex
    Indexer -->|"authenticated reads after unlock"| CoreFacade
    CoreFacade -.->|"invalidation after canonical commit"| Indexer

    Agent --> SoulWriter
    SoulWriter -->|"approved memory promotion only"| Soul
    CoreFacade -.->|"stable corefs provenance"| SoulWriter

    Broker --> Grants
    Broker --> Credentials
    API --> Credentials
    Device -->|"contains instance-scoped runtime path"| Runtime
    Device --> Grants
```

There is deliberately no CoreFS-to-host-files edge and no Runtime-to-canonical-catalog write edge. CoreFS cannot escape into an Animus folder, and Runtime cannot become canonical authority.

### Authority summary

| State | Canonical authority | Portable | Rebuildable |
|---|---|---:|---:|
| ANIMA identity, durable memories, self-model, relationship, growth | SQLCipher Soul | yes | no |
| Diary, notes, folders, threads, messages, gallery, attachments, tasks, portable preferences | encrypted CoreFS catalogs and objects | yes | no |
| Runs, jobs, queues, checkpoints, blind tokens, safe operational metadata | Runtime PostgreSQL | no | yes |
| Decrypted text index and semantic vectors | unlock-scoped process memory | no | yes |
| Client executable grants and machine configuration | authenticated platform app data | no | yes or reapproved |
| Provider, connector, and daemon secrets | OS credential store | no | reconfigured |

## 2. Unlock, Startup, and Progressive Reindexing

The Core becomes navigable before full-text and semantic indexing complete. A missing Runtime is a rebuild event, not data loss.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant UI as Desktop
    participant Auth as Unlock coordinator
    participant Manifest as Manifest keyslots
    participant Native as anima-core PyO3 and anima-corefs crypto
    participant Soul as SQLCipher Soul
    participant Core as anima-corefs
    participant PG as Runtime PostgreSQL
    participant Index as Progressive indexer

    User->>UI: Enter passphrase or recovery phrase
    UI->>Auth: Unlock request
    Auth->>Manifest: Read declared keyslot records and bounded KDF parameters
    Manifest-->>Auth: Wrapped SQLCipher and FRK slots plus generation metadata
    Auth->>Native: Submit secret and declared wrapped slot records
    Native->>Native: Derive KEK, authenticate generation, and unwrap roots
    Native-->>Auth: Validated SQLCipher key plus unlocked CoreFS session
    Auth->>Soul: Open and validate Soul
    Auth->>Core: Use unlocked session to validate fs/HEAD and committed catalog
    Core-->>Auth: Catalog generation and degraded-object report
    Auth->>PG: Resolve Core and local-instance binding

    alt Runtime missing or incompatible
        PG-->>Auth: Empty fresh Runtime
    else Runtime generation is usable
        PG-->>Auth: Safe checkpoints and generation metadata
    end

    Auth-->>UI: Catalog ready or catalog-ready-degraded
    UI-->>User: Navigation and canonical reads available

    Auth->>Index: Reconcile committed catalog generation
    Index->>Core: Stream changed text and metadata after unlock
    Index->>PG: Persist safe checkpoints and blind tokens
    Index->>Index: Build plaintext lexical index in memory
    Index-->>UI: Text-search readiness event
    Index->>Index: Build semantic vectors in memory
    Index-->>UI: Ready or degraded-ready event

    Note over Auth,Index: Lock, logout, expiry, or shutdown revokes streams and clears FRKs, DEKs, plaintext indexes, vectors, and query state.
```

## 3. Explicit HostFS and CoreFS Tool Routing

The shared library reuses algorithms, not authority. Tool names and backend handles select the boundary explicitly; a path string never causes automatic routing.

```mermaid
flowchart LR
    Intent["Agent or user file intent"] --> Choice{"Which explicit tool was called?"}

    Choice -->|"read_file, grep, apply_patch"| HostTool["Animus host tool handler"]
    Choice -->|"corefs_read, corefs_grep, corefs_apply_patch"| CoreTool["CoreFS tool handler"]

    HostTool --> HostAuth["Workspace containment<br/>Allow, Ask, or Deny"]
    HostAuth --> HostCap["HostFS capability descriptor<br/>OS path and atomicity semantics"]
    HostCap --> Shared["anima-file-tools"]
    Shared --> HostBackend["Explicit HostFS backend handle"]
    HostBackend --> HostFiles["Authorized host files"]

    CoreTool --> Unlock{"Core unlocked?"}
    Unlock -->|"no"| Locked["locked error"]
    Unlock -->|"yes"| Policy["Principal plus inherited folder policy<br/>explicit deny wins"]
    Policy --> CoreCap["CoreFS capability descriptor<br/>stable IDs and atomic catalog commit"]
    CoreCap --> Shared
    Shared --> CoreStore["anima-corefs encrypted backend"]

    HostTool -.->|"rejects corefs URI"| RouteError["typed backend or path-domain error"]
    CoreTool -.->|"rejects host path"| RouteError
```

## 4. Read, Grep, Search, and Atomic Mutation

### Read and discovery paths

```mermaid
flowchart TD
    Request["Unlocked CoreFS request"] --> Authz["Resolve principal, stable folder ID, inherited policy, and revision"]
    Authz --> Op{"Operation"}

    Op -->|"list, walk, glob"| CatalogRead["Read committed catalog generation"]
    Op -->|"read"| ObjectRead["Stream authenticated object chunks<br/>bounded output and revision"]
    Op -->|"grep"| Grep["Authoritative bounded scan<br/>literal or linear-time regex"]
    Op -->|"search"| Ready{"Runtime index generation ready?"}

    Ready -->|"yes"| Ranked["Lexical or semantic ranked results<br/>generation and readiness included"]
    Ready -->|"partial"| Partial["Partial or degraded result<br/>never silently substitute grep"]
    Ready -->|"no coverage"| NotReady["Index not ready<br/>readiness status and no ranked result"]

    CatalogRead --> Result["Stable ID, logical path, revision, hash, cursor"]
    ObjectRead --> Result
    Grep --> Result
    Ranked --> Result
    Partial --> Result
    NotReady --> ReadinessResult["Readiness-only response<br/>zero items and current generation status"]
```

### One-generation write and patch transaction

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant Tools as anima-file-tools
    participant Core as anima-corefs
    participant Lock as Core-wide commit lock
    participant Store as Objects, catalogs, fs/HEAD, and cutover markers
    participant Index as Runtime indexer

    Caller->>Tools: write or multi-file apply_patch with expected revisions
    Tools->>Tools: Parse into typed mutation plan
    Tools->>Core: Submit complete plan
    Core->>Core: Resolve every stable ID and path
    Core->>Core: Preflight policy, deny rules, revisions, collisions, formats, and limits
    Core->>Core: Prepare encrypted immutable object revisions
    Core->>Store: Flush and atomically publish immutable object revisions
    Core->>Lock: Acquire exclusive commit lock
    Core->>Store: Reload authoritative generation
    Store-->>Core: Latest catalog and fs/HEAD
    Core->>Core: Revalidate every precondition

    alt Any precondition changed or failed
        Core->>Lock: Release lock
        Core-->>Caller: Structured conflict with unreferenced object garbage and no catalog generation
    else Complete plan remains valid
        Core->>Store: Publish encrypted catalog generation
        Core->>Store: Atomically replace and flush fs/HEAD
        opt First accepted post-migration mutation
            Core->>Store: Publish exact CUTOVER_RECEIPT then CUTOVER_COMPLETE
            Note over Core,Store: HEAD is already authoritative; marker failure returns recovery pending.
        end
        Core->>Lock: Release lock
        Core-->>Index: Invalidate committed generation
        Note over Core,Index: Index failure never rolls back the canonical commit.
        Core-->>Caller: New revisions, hashes, generation, atomic true
    end
```

### Catalog-bound key rotation

FRK activation is split deliberately across the credential coordinator and CoreFS. The credential side must first durably write and independently verify both pending password and recovery keyslots. Only then may it pass session-owned old/pending FRK subkeys into the CoreFS rotation transaction; CoreFS never persists those raw keys and does not promote manifest state itself.

```mermaid
sequenceDiagram
    autonumber
    participant Auth as Credential coordinator
    participant Core as anima-corefs
    participant Lock as Core-wide commit lock
    participant Store as Catalogs and fs/HEAD
    participant Runtime as Runtime indexer

    Auth->>Auth: Reload and verify pending password + recovery FRK slots
    Auth->>Core: Old/pending in-memory keyring + expected generation
    Core->>Lock: Acquire exclusive commit lock
    Core->>Store: Reload and authenticate HEAD/catalog with declared FRK version
    Core->>Core: Rewrap every retained Object DEK, including tombstones, under pending OKWK
    Note over Core: Object ciphertext, revision, hash, and key epoch stay unchanged
    Core->>Store: Publish complete encrypted catalog under pending FRK
    Core->>Store: Atomically replace and flush fs/HEAD with pending FRK version
    Core->>Lock: Release lock
    Core-->>Runtime: Invalidate committed generation
    Core-->>Auth: Published generation and required FRK version
    Auth->>Auth: Promote pending FRK; retain prior FRK decrypt-only
```

`fs/HEAD.requiredFrkVersion` is only a key-selection hint until the referenced encrypted catalog authenticates. During activation, a versioned in-memory keyring authenticates the new HEAD/catalog and the older immutable cutover markers with their own keys. After an old cutover key is legitimately retired, a strictly later authenticated catalog carrying the irreversible cutover marker preserves lineage; equal-generation or rollback-only inputs still require the old key and fail closed.

Targeted Object-DEK rotation is separate from FRK rewrap: it streams the authenticated current envelope into a complete new revision with a fresh Object DEK, fresh nonces, and incremented object-key epoch, then uses the normal prepared-revision/catalog commit path. A crash during FRK publication leaves the old HEAD authoritative or the complete pending-key HEAD readable—never a partially visible catalog generation.

Old-key retirement is not implied by successful activation. Authenticated retention inventory must show that no retained catalog HEAD or wrapped Object DEK still requires the retiring version, and a verified backup under the active version must exist. Physical catalog/object pruning and keyslot deletion remain the separately authenticated PCF-010 operation.

## 5. Folder Ownership, ANIMA Access, and Client Trust

Folder names and paths may change. Stable IDs and roles preserve app bindings; policy inheritance controls principals.

```mermaid
flowchart TB
    User["User owner<br/>ultimate CoreFS authority"]
    Root["CoreFS root"]
    Journal["Journal folder<br/>role core.journal<br/>owner user, ANIMA write"]
    Notes["Notes folder<br/>role core.notes<br/>owner user, ANIMA write"]
    Gallery["Gallery folder<br/>role core.gallery<br/>ownership and ANIMA access configured independently"]
    Shared["Conversations folder<br/>role core.conversations<br/>owner shared, ANIMA manage"]
    Reflection["ANIMA reflections<br/>owner anima, ANIMA manage"]
    Custom["Custom user folder<br/>owner user, ANIMA write by default"]
    Import["Imported external folder<br/>owner user, ANIMA none by default"]

    User --> Root
    Root --> Journal
    Root --> Notes
    Root --> Gallery
    Root --> Shared
    Root --> Reflection
    Root --> Custom
    Root --> Import

    Journal -->|"rename or move"| JournalMoved["Renamed or moved Journal view<br/>same folder ID and core.journal role"]

    Package["Installed client package"] --> Broker["Capability broker reads canonical package ID,<br/>computes payload digest, verifies optional publisher signature,<br/>and assigns the local installation principal"]
    Broker --> Verify{"Verified installation and current approval?"}
    Verify -->|"no"| Reject["Reject, collide, revoke, or require reapproval"]
    Verify -->|"yes"| Grant["Device-local grant<br/>Core, instance, installation principal, package ID,<br/>payload digest, optional verified publisher identity,<br/>folder, scope, generation"]
    Grant --> Capability["Short-lived audience-scoped capability"]
    Capability --> ClientFolder["Portable client namespace<br/>client package ID and role"]
    ClientFolder --> Child["Client-created descendants<br/>inherit parent ownership and policy"]

    Deny["Explicit deny"] -.->|"overrides inherited or direct allow"| Capability
    Lock["Core lock or grant generation change"] -.->|"revokes handles and token renewal"| Capability
    Transfer["Core moves to another machine"] -.->|"folders and metadata transfer; executable grant does not"| ClientFolder
```

| Scope | Read | Create/edit | Rename/move/trash/restore | Policy/grants/reserved roles | Permanent purge/key retirement |
|---|---:|---:|---:|---:|---:|
| User after unlock | yes | yes | yes | yes | recent reauthentication and bound confirmation |
| ANIMA `none` | no | no | no | never | never |
| ANIMA `read` | yes | no | no | never | never |
| ANIMA `write` | yes | yes | no | never | never |
| ANIMA `manage` | yes | yes | yes | never | never |
| Client `read`/`write`/`manage` | by approved scope | by approved scope | `manage` only | never | never |

Normal deletion moves content into encrypted trash. `purge(trash_id, expected_trash_revision, confirmation)` is a separate user-only maintenance operation for already-trashed content after recent reauthentication; ANIMA and clients cannot call it even with `manage`.

## 6. Local Export, Transfer, Recovery, and Runtime Rebuild

One versioned streaming container supports complete, Soul-only, and CoreFS-only local artifacts. Cloud upload and sync remain out of scope.

```mermaid
flowchart TD
    Live["Unlocked live ANIMA CORE"] --> Barrier["Short write barrier<br/>pin Soul and CoreFS generations"]
    Barrier --> Kind{"Artifact payload kind"}

    Kind -->|"full"| Full["anima-core-{timestamp}.anima<br/>coherent Soul plus CoreFS pair"]
    Kind -->|"soul"| SoulOnly["anima-core-soul-{timestamp}.anima<br/>Soul plus required keyslots"]
    Kind -->|"fs"| FsOnly["anima-core-fs-{timestamp}.anima<br/>catalogs, reachable objects, FRK keyslots"]

    Passphrase["Archive passphrase<br/>may reuse unlock text but never key bytes"] --> ArchiveKey["Validate fixed KDF profile<br/>Argon2id plus archive-specific HKDF"]
    ArchiveKey --> Independent["Independent archiveKey<br/>never Soul key, FRK, catalog key, or Object DEK"]

    Full --> Framing["Bounded V2 header, encrypted manifest,<br/>record inventory, chunk AAD, and footer"]
    SoulOnly --> Framing
    FsOnly --> Framing
    Independent --> Stream["Bounded authenticated stream<br/>partial file until footer verifies"]
    Framing --> Stream
    Stream --> Media{"Destination capability"}
    Media -->|"large file supported"| Single["One .anima file"]
    Media -->|"single-file limit"| Multi["Authenticated bounded-volume set"]
    Single --> Drive["Local hard drive or removable drive"]
    Multi --> Drive

    Drive --> Import["Stream into same-volume staging Core"]
    Import --> Verify["Authenticate header, inventory, chunks, footer, generations, and capacity"]
    Verify --> Valid{"Complete and valid?"}
    Valid -->|"no"| Preserve["Reject staging<br/>preserve active Core unchanged"]
    Valid -->|"yes"| Destination{"New destination or replacement?"}
    Destination -->|"new"| NewDirectory["Fsync staging and parent<br/>atomically rename final Core directory"]
    Destination -->|"replacement"| Activate["Lock both paths, atomically switch active-Core registry,<br/>retain old Core for rollback"]

    NewDirectory --> RestoreKind{"Restored payload"}
    Activate --> RestoreKind
    RestoreKind -->|"full"| Normal["Normal unlock and startup"]
    RestoreKind -->|"soul"| Missing["filesystem_missing degraded mode"]
    RestoreKind -->|"fs"| Recovery["Authenticated browse and export-only recovery mode"]

    Normal --> FreshRuntime["Create or validate machine-local Runtime"]
    Missing --> SoulRuntime["Start identity and cognition in filesystem_missing<br/>no CoreFS catalog or content index"]
    SoulRuntime -->|"explicit confirmation, current passphrase, verified recovery phrase,<br/>and complete password plus recovery FRK wrappers"| NewFs["Optional new empty CoreFS<br/>not attachment of the missing CoreFS"]
    Recovery --> RecoveryTools["Restricted CoreFS browse and export<br/>no complete ANIMA agent runtime"]
    FreshRuntime --> Reindex["Catalog ready, then text and semantic rebuild"]
```

## 7. Cross-Graph Invariants

1. SQLCipher Soul contains ANIMA's internal continuity, not canonical app-feature records.
2. CoreFS catalogs and objects are the only canonical authority for portable user content.
3. Runtime PostgreSQL may accelerate and coordinate work, but deleting it cannot delete canonical content.
4. CoreFS and HostFS share bounded algorithms while retaining distinct tools, principals, paths, and transaction guarantees.
5. All CoreFS access requires unlock plus principal policy; explicit deny wins and lock revokes every handle.
6. Multi-file CoreFS mutations publish one complete catalog generation or none.
7. Client content is portable; executable authorization is device-local and must be reapproved after transfer or package-digest change.
8. Soul and CoreFS use separate root keys and can enter explicit independent recovery states.
9. Export/import is local, streaming, authenticated, capacity-preflighted, and staged before activation.
10. Memory promotion from CoreFS into Soul occurs only through the existing candidate, consolidation, and Soul Writer boundary with stable provenance.
11. The transfer container derives an archive-specific key from bounded KDF parameters; it never reuses the Soul key, FRK, catalog key, or Object DEKs as its archive payload key.
12. FRK activation publishes one complete next catalog/HEAD generation; old FRKs remain decrypt-only until authenticated retention and backup gates permit PCF-010 retirement.
