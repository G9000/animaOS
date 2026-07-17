# Portable Core Filesystem Design

**Date:** 2026-07-12  
**Status:** Approved<br>
**Scope:** Define animaOS's ANIMA CORE subsystem: separate agent-internal Soul state from portable application content, keep runtime PostgreSQL disposable and machine-local, and support local streaming transfer/recovery  
**PRD:** [Portable Core Filesystem v1](../../prds/portable-core-filesystem-v1.md)

**Security design:** [Portable Core Key Hierarchy and Rotation](2026-07-12-portable-core-key-hierarchy-design.md)

**Architecture diagrams:** [ANIMA CORE Filesystem Target Architecture](../../architecture/system/anima-core-filesystem.md)

---

## 1. Goal

Turn `.anima/` into the complete portable identity and user-owned experience, excluding device capabilities and external credentials, with two canonical forms of durable state:

1. **Soul state** in SQLCipher, containing only ANIMA's internal continuity.
2. **CoreFS objects** stored as encrypted files, containing user-visible application data in logical Markdown, JSON, JSONL, HTML, and binary formats.

Embedded PostgreSQL remains the operational engine, but its data directory moves outside `.anima/`. It indexes and processes the Core after unlock and can be deleted and rebuilt without losing canonical data.

## 2. Non-Goals

- Replace SQLCipher for Soul state.
- Remove PostgreSQL as the runtime engine.
- Add cloud upload, cloud backup, cloud sync, remote replication, or multi-device concurrent writes. This design covers local disks, removable media, and machine-to-machine transfer only.
- Attach a CoreFS-only artifact to a Soul in V1. Recovery-mode browsing/export is supported; safe reattachment requires a later lineage/conflict/atomic-pair design.
- Expose decrypted Core files directly to arbitrary host applications in V1; any future mount must use an authenticated adapter with explicit lifecycle and permission controls.
- Give the agent unrestricted shell access to `.anima/` or the host filesystem.
- Change the meaning of memory promotion, Soul Writer, or consolidation.
- Preserve legacy app-specific database schemas as long-term compatibility APIs.

## 3. Terminology

| Term | Meaning |
|---|---|
| animaOS | The complete product/application; it is not renamed by this work |
| ANIMA CORE | animaOS's portable encrypted subsystem and transfer-artifact family, comprising Soul plus CoreFS while excluding Runtime/device/credential state |
| Core | The live `.anima/` directory representation of ANIMA CORE: manifest, Soul, and encrypted user-owned content |
| Soul | SQLCipher compartment containing ANIMA's internal identity and durable cognitive state |
| Core Filesystem | Public architectural name for encrypted, portable, file-backed application state inside the Core |
| CoreFS | Service/API implementing the Core Filesystem; V1 is virtual and unmounted, with an authenticated OS mount adapter left for a future version |
| Core object | One encrypted canonical CoreFS unit |
| Logical path | Human/agent-facing path visible only after unlock, such as `diary/2026/07/entry.md` |
| Physical object | Opaque encrypted file stored on disk under `.anima/objects/` |
| Runtime | Machine-local PostgreSQL, caches, logs, temporary state, and active execution state outside `.anima/` |
| Catalog readiness | Minimum index state needed to navigate and open CoreFS files |
| Deep index | Full-text, chunk, and semantic indexes that may finish after the UI becomes usable |
| ANIMA CORE artifact | Local authenticated streaming export using format `anima_core_v2` and payload kind `full`, `soul`, or `fs` |
| Folder role | Stable, unique semantic binding used by animaOS or an approved client; independent of a folder's mutable name and parent |
| Principal | The user, ANIMA, or an approved client acting through CoreFS |

## 4. Architectural Invariants

1. `.anima/` is sufficient to transfer all canonical Soul and portable app content.
2. SQLCipher is sufficient to answer "who is ANIMA?" and contains no canonical app-feature records.
3. Encrypted Core files are sufficient to answer "what user-owned content exists?"
4. PostgreSQL is never required to recover canonical content.
5. No canonical user-private content is plaintext on disk.
6. The desktop and agent mutate files through one CoreFS service.
7. Runtime never writes directly to Soul; only consolidation/Soul Writer can promote durable internal state.
8. Indexing failure cannot invalidate a successful canonical write.
9. Every object mutation is atomic, revision-checked, and recoverable after interruption.
10. Startup readiness is staged; embeddings never block basic navigation.
11. Soul and CoreFS use separate root keys, generations, verification, and recovery states; loss of one compartment does not make the other's authentic bytes undecryptable.
12. A full transfer commits one coherent Soul/CoreFS generation pair; partial recovery never presents one compartment as a complete ANIMA.
13. CoreFS is a customizable folder tree after unlock; empty folders, rename, and move are durable catalog operations.
14. Stable IDs and roles, never hardcoded display paths, bind app features to folders.
15. HostFS and CoreFS operations share bounded algorithms but remain separate, explicit authority domains.

## 5. Target Physical Layout

```text
.anima/
  manifest.json                    # non-personal bootstrap/encryption metadata
  soul/
    soul.db                        # SQLCipher: agent internal continuity only
  fs/
    HEAD                           # generation, catalog hash, envelope version, required FRK version
    CUTOVER_RECEIPT                # authenticated first-cutover HEAD receipt, published after HEAD
    CUTOVER_COMPLETE               # authenticated proof that first-cutover marker finalization completed
    catalogs/
      <generation>.catalog.acore   # encrypted logical-path/object map
  objects/
    00/
      <opaque-object-id>-r<revision>.acore
    01/
      <opaque-object-id>-r<revision>.acore
  recovery/
    ...                            # existing recovery material, no plaintext secrets
```

Machine-local state:

```text
%LOCALAPPDATA%/AnimaOS/
  cores/<core-id>/
    instance-registry.json         # machine-local Core-path/filesystem-identity leases
    instances/<local-instance-id>/
      runtime/
        pg_data/
        pg_logs/
      cache/
      index-state/
      client-grants/                  # authenticated device-local client identity/scope records
      health-logs/
  daemon/                          # machine-wide local daemon files/logs; no Core content
```

Equivalent platform-native application-data roots are used on macOS and Linux. A machine-local registry binds the stable portable `core-id`, canonical resolved Core path/filesystem identity, and a generated local-instance ID. Moving the same filesystem object rebinds its path; a divergent copy with the same `core-id` receives a separate instance directory or is refused while a conflicting live lease exists. Runtime, caches, blind tokens, index checkpoints, and health logs are all instance-scoped. An explicit `ANIMA_RUNTIME_DATABASE_URL` must contain and verify an instance-binding row and is rejected if another Core instance already owns it. No machine path or local-instance ID is written into the portable Core.

The object store uses opaque physical names so diary titles, dates, logical folder names, and conversation subjects are not exposed while locked. The unlocked service reconstructs the Core Filesystem from the committed encrypted catalog. Object revisions and catalog generations are immutable; `fs/HEAD` is the only mutable publication pointer.

### 5.1 Implementation Layers

Durability and filesystem mechanics are Rust-owned; product semantics remain Python-owned:

```text
packages/anima-file-tools
  bounded walk/glob/grep/read, patch parsing/planning, pagination, limits, typed errors
        | explicit backend trait
        +-------------------------+
        |                         |
Animus HostFs backend      packages/anima-corefs
host tools only            encrypted catalog/object backend
                                  |
                         existing anima-core PyO3 extension
                                  |
                      Python CoreFS/domain/API/indexing services
```

- `anima-file-tools` is storage-agnostic. It accepts an explicit backend handle and capability description; it does not inspect a path and choose a backend.
- `anima-corefs` owns envelope streaming, key derivation/wrapping and blind-token primitives, encrypted catalogs, stable folder entries, policy evaluation, optimistic revisions, the Core-wide lock, atomic catalog publication, trash, and restore.
- `anima-core` wraps/re-exports the CoreFS Rust API through the existing native Python extension so animaOS does not ship a second competing extension module.
- Python owns manifest orchestration, migrations, domain codecs, FastAPI schemas/routes, runtime indexing, and feature-specific projections.
- Animus uses the same file-tool contracts with a HostFS backend and keeps its existing workspace/sandbox authorization in front of that backend.

The crates expose small documented public APIs and focused modules. They are not folded into the already memory-focused `anima-core` implementation merely for convenience.

## 6. Soul Placement Rule

### 6.1 Belongs in SQLCipher Soul

- identity and persona blocks
- agent profile and self-model blocks
- durable long-term memories, claims, evidence, tags, and episodes
- learned model of the user and relationship
- durable emotional patterns and growth history
- enduring internal intentions/foresight that define continuity
- high-confidence consolidated knowledge graph state
- internal key records used only by Soul encryption domains

### 6.2 Does Not Belong in SQLCipher Soul

- diary entries, folders, covers, and attachments
- user notes
- threads, messages, runs, and execution steps
- gallery assets and user-visible annotations
- user-created product tasks
- provider/model configuration
- portable appearance and interaction settings
- integration links and tokens
- search indexes, embeddings, chunks, access logs, and queues
- authentication/session artifacts that exist only to operate the local app

Some existing tables contain mixed semantics. Migration must classify records by meaning, not by their current Python module or table name.

### 6.3 Account, Unlock, and Key Placement

The current `users`, `user_keys`, and manifest `user_index` records do not remain in SQLCipher merely because authentication currently queries them there.

Target placement:

| Current data | Target |
|---|---|
| Core ID and opaque owner ID | non-personal manifest bootstrap metadata |
| KDF parameters, wrapped SQLCipher Soul key, wrapped Filesystem Root Key, recovery-wrapped root keyslots | manifest/recovery keyslot records; ciphertext and non-secret parameters only |
| username, display name, birthday, gender, age, and other account-profile fields | encrypted `account-profile` Core object |
| password hash used only for legacy database login | removed after key-unwrapping authentication cutover; successful authenticated unwrap becomes the local unlock proof |
| plaintext manifest username/user index | removed after migration; optional remembered username stays device-local and is not required to unlock |
| unlock/session token | process/session state outside the Core; never portable canonical data |

V1 remains a single-owner Core. The manifest can identify the opaque owner before unlock without exposing username or profile data. After the passphrase unwraps the Soul and Filesystem keys, the encrypted account-profile object supplies user-facing account fields. Migration must preserve the legacy numeric user ID as an internal compatibility identifier until all references use stable Core IDs.

This is an explicit product tradeoff: before Filesystem Root Key unlock, the UI cannot show the private display name, avatar, birthday, profile language, or other account customization. The locked surface uses neutral ANIMA branding plus operating-system locale, accessibility, and input settings stored as device capabilities. Migration identity uses `core_id` and opaque owner ID. Multi-owner selection is out of scope for V1 and must not be approximated by putting plaintext usernames back into the manifest.

## 7. Canonical Core Object Model

### 7.1 Envelope

Each `.acore` object has a small non-personal binary header, one authenticated encrypted metadata frame, and zero or more independently authenticated body chunks. Envelope V1 is streamable and seekable without writing decrypted temporary files.

Header fields:

- magic bytes and envelope version
- opaque Core object ID (UUIDv7 or equivalent sortable unique ID)
- key domain and key version
- cipher suite identifier
- metadata-frame nonce, ciphertext length, and authentication tag
- chunking version, fixed plaintext chunk size, chunk count, and total plaintext body length

The authenticated metadata frame contains canonical JSON (maximum 1 MiB decrypted):

```json
{
  "schemaVersion": 1,
  "kind": "account-profile|folder|diary|draft|note|thread|message-segment|gallery-asset|attachment|knowledge-source|task|preferences",
  "objectId": "019f...",
  "revision": 4,
  "createdAt": "2026-07-12T12:00:00+08:00",
  "updatedAt": "2026-07-12T12:30:00+08:00",
  "contentType": "text/markdown",
  "metadata": {},
  "bodyEncoding": "utf-8|binary",
  "bodyLength": 123456,
  "bodySha256": "...",
  "chunkPlaintextSize": 4194304,
  "chunkCount": 1
}
```

Body bytes are not base64-embedded in JSON. Envelope V1 uses 4-MiB plaintext chunks and supports at most 2,048 chunks / 8 GiB per object; larger input is rejected before publication with an explicit capacity error. A zero-length body has zero chunks. Every metadata/body frame uses AES-256-GCM with the Object DEK and a fresh independent random 96-bit nonce; a writer rejects a repeated nonce within the object revision and never derives one from an object ID, revision, timestamp, or chunk index.

The base object AAD binds `core_id`, object ID, content kind, envelope version, object-key epoch, and revision. Metadata-frame AAD extends it with `frame=metadata` and `chunkingVersion=1`. After encrypting metadata, the writer hashes the complete metadata frame (nonce, ciphertext, and tag). Each body-chunk AAD extends the same base with `frame=body`, that metadata-frame hash, zero-based chunk index, declared chunk count, plaintext offset, plaintext length, total body length, and final-chunk flag. This makes substitution across objects/revisions, truncation, duplication, reordering, and splicing fail authentication or framing validation.

A reader authenticates metadata and validates all bounds before allocating. For a full read, it authenticates each chunk before releasing that chunk, maintains the body SHA-256, and requires the declared chunk count/length/final flag and final body hash to match. A range reader first authenticates metadata, then authenticates every chunk intersecting the range before returning bytes; it reports chunk/range integrity, not a whole-body verification claim unless all chunks were read. Seekable consumers such as PDF extraction use this bounded authenticated range reader and an unlock-scoped memory cache, never a plaintext host-path copy. Tests cover empty/single/multi-chunk bodies, maximum-bound rejection, nonce collision injection, tag corruption, metadata tampering, truncation, duplicate/reordered chunks, wrong offsets/final flags, full-stream hash failure, and verified range reads.

Logical paths do not live inside object payloads. The committed encrypted catalog is their sole authority. This allows a move to publish a new catalog generation without rewriting unchanged encrypted content and prevents two conflicting path sources.

### 7.2 Encryption Domains

- Soul continues to use its SQLCipher key.
- CoreFS uses a separate random Filesystem Root Key plus random per-object DEKs defined by the security design.
- The Core passphrase/recovery flow wraps both keys independently.
- Content object AEAD base associated data binds exactly `core_id`, object ID, content kind, envelope version, object-key epoch, and revision; per-frame AAD extends that base with the normative framing fields in Section 7.1. Neither binds the FRK version, so FRK rotation can rewrap Object DEKs without rewriting payload ciphertext.
- Targeted object-key rotation writes a new encrypted revision/key epoch and never mutates ciphertext in place; FRK rotation rewraps live Object DEKs through a new catalog generation.
- Temporary plaintext exists only in bounded process memory and is never written to normal temporary directories.

This separation ensures CoreFS access does not grant raw SQLCipher access and permits future Filesystem Root Key rotation without rewriting Soul state.

The key hierarchy, per-object DEKs, compromise boundaries, recovery wrappers, and crash-resumable rotation protocol are normative in the linked security design. This parent document does not permit one global payload DEK to encrypt every object directly.

### 7.3 Logical Formats

| Kind | Logical content | Mutation shape |
|---|---|---|
| Account profile | versioned JSON | atomic revision replacement |
| Folder | first-class catalog entry with stable ID, parent, role, owner, access policy, and namespaced metadata | atomic catalog revision; survives while empty |
| Diary | sanitized HTML plus typed metadata | atomic document replacement or patch; embedded media extracted to CoreFS objects |
| Draft | Markdown or JSON draft plus target reference | atomic replacement; explicit promotion/removal on save |
| Note | Markdown or sanitized HTML plus typed metadata | atomic document replacement or patch |
| Thread | JSON metadata | atomic metadata revision |
| Messages | ordered JSONL-compatible encrypted segments | append segment; explicit edit/delete event if supported |
| Gallery asset | binary payload plus JSON metadata | immutable binary, revisioned metadata |
| Attachment | content-addressed binary plus metadata | immutable binary; reference-counted links |
| Task | JSON document | atomic revision replacement |
| Preferences | versioned JSON document | atomic revision replacement |

Diary migration is lossless against the current Tiptap/Journal contract. Plain-text legacy bodies are HTML-escaped and wrapped as paragraphs; legacy HTML is normalized through the same versioned sanitizer used for new writes. An inline `data:` image is decoded, MIME- and size-validated, content-deduplicated into an encrypted binary CoreFS object, and replaced with a stable `corefs://object/<object-id>` URI before the diary revision is published. Covers, attachment-only entries, cover-only entries, folder membership, and rich formatting must survive conversion. The unlocked desktop API resolves CoreFS media URIs for rendering; canonical diary HTML never retains base64 `data:` payloads.

Messages are segmented rather than stored in one ever-growing encrypted file. The unlocked API projects the segments as one ordered JSONL-compatible event stream.

Every logical directory is a first-class catalog entry, including an empty custom folder. A folder has `folderId`, nullable `parentId`, mutable `name`, optional stable `role`, `owner`, `agentAccess`, policy overrides, and namespaced extension metadata. Documents reference folders by stable `corefs://folder/<folder-id>` URI rather than relying on a path prefix. Rename or move changes catalog presentation state without changing the ID, role, or references.

Reserved animaOS roles use the `core.*` namespace. V1 binds Journal to `core.journal`, standalone Notes to `core.notes`, Conversations to `core.conversations`, and Gallery to `core.gallery`. Each reserved role resolves to one stable folder ID across rename, move, restart, and transfer. Approved clients use `client:<client-id>:<role>`. A role is unique within a Core. Clients cannot claim `core.*`, and unknown app roots are never recovered by guessing a folder name. Client-created folder entries, roles, and namespaced metadata are portable; the executable authorization grant is not.

Folder defaults are policy, not special-case storage code:

| Origin/example | `owner` | Default ANIMA access |
|---|---|---|
| Folder created by ANIMA in an ANIMA-owned root | `anima` | `manage` |
| User-created folder inside CoreFS | `user` | `write` |
| Imported external folder | `user` | `none` |
| Approved client-created descendant | inherits parent | inherits parent; client cannot choose policy |
| Private Journal/Diary | `user` | `write` |
| Standalone Notes | `user` | `write` |
| Conversations | `shared` | `manage` |
| ANIMA reflections | `anima` | `manage` |

`write` permits reading and creating/editing descendants but not renaming, moving, trashing, restoring, or changing policy. `manage` adds those structural operations. The user retains control of all CoreFS content. Policy inherits down the tree; explicit deny wins over any allow. ANIMA cannot elevate access to a user-owned folder: content it creates there inherits the user-owned boundary and cannot become an `anima/manage` island. Client-created descendants likewise inherit parent ownership/policy, and client principals cannot choose ownership, edit policy, or edit their own capability.

Operation authorization is explicit:

| Operation | User after unlock | ANIMA | Approved client |
|---|---|---|---|
| discover/stat/read | allowed | `read`, `write`, or `manage` | granted `read`, `write`, or `manage` scope |
| create/mkdir/write/apply patch | allowed | `write` or `manage` | granted `write` or `manage` scope; descendants inherit policy |
| rename/move/trash/restore | allowed | `manage` | granted `manage` scope |
| set owner/access/deny, bind reserved role, grant/revoke client | user-only | never | never |
| permanent purge/key retirement | recent user reauthentication plus bound confirmation | never | never |

There is no hidden `admin` meaning inside `manage`: it is structural authority only. Policy, ownership, reserved-role administration, client grants, permanent purge, and key retirement are always user-only operations even when ANIMA or a client has `manage`.

#### Conversation segment contract

- A thread owns an ordered chain of message-segment objects.
- Each message has a stable message ID and `corefs://thread/<thread-id>/message/<message-id>` URI. A message is not required to be its own physical Core object.
- Records are events: `message.created`, `message.edited`, and `message.deleted`. Every event has a stable event ID, message ID, monotonic `messageVersion`, and (except create) `expectedPriorEventId`/`expectedPriorVersion`. The read model projects the latest valid transition for each stable message ID; edits and deletes never rewrite historical segment bytes in place.
- A segment accepts at most 256 events or 1 MiB of decrypted canonical JSONL payload, whichever comes first. The next append rolls to a new segment when either limit would be exceeded.
- Each segment records thread ID, segment ordinal, first/last logical sequence, event count, previous segment ID/hash, and its own canonical hash. The catalog maps the thread to the ordered segment chain.
- Logical sequence, not timestamp, determines display order. New messages receive the next sequence while holding the per-thread append lock. Imported records preserve a trustworthy source sequence; otherwise migration assigns deterministic order using normalized timestamp, source precedence, and stable source ID, and quarantines unresolved collisions.
- Concurrent appends serialize through a per-thread append lock, then revalidate the tail segment revision while holding the Core-wide catalog commit lock. A stale tail retries from the new committed tail rather than dropping or overwriting an event.
- `message.created` requires that the message ID does not exist and publishes version 1. `message.edited` requires a live message whose current event/version exactly matches its precondition, then publishes version `N+1`. `message.deleted` has the same compare-and-swap precondition and publishes terminal version `N+1`.
- Deletion is terminal in V1: later edits, deletes, or recreation under the same message ID are rejected. An undo/restore operation creates a new message ID with optional provenance to the deleted message.
- A stale edit/delete returns a structured conflict containing the current event ID/version/state. It is never appended merely because it acquired the thread lock after another transition.
- Partial corruption is isolated at segment granularity. Valid earlier/later segments remain available, while the thread is marked degraded with an explicit missing/corrupt sequence range.
- Segment rollover and event projection use the same implementation for live chat, migration, archive import, and CoreFS tools.

### 7.4 Committed Catalog Generations

The authoritative CoreFS inventory is an encrypted immutable catalog generation. It maps each logical path to object ID, revision, physical object name, content hash, kind, and deletion state. Tombstones are catalog entries, not independently published loose files.

Catalog publication uses one short Core-wide commit lock in V1. Per-object locks protect edit preparation, but they are not sufficient because every mutation changes the shared catalog. Publishing one mutation follows this order:

1. Write the new immutable object revision to a sibling temporary file.
2. Flush and atomically publish the object revision; an interrupted write leaves at most an unreferenced object.
3. Acquire the Core-wide catalog commit lock.
4. Reload `fs/HEAD` and the current committed catalog; revalidate source/destination paths and every expected object revision against this latest generation.
5. If revalidation conflicts, release the lock and return a revision/path conflict. The unreferenced new object is safe garbage.
6. Build the next encrypted catalog generation from the reloaded catalog plus the mutation.
7. Flush and atomically publish the catalog generation.
8. Atomically replace `fs/HEAD` with the new generation number, catalog hash, envelope version, and required FRK version, then flush the directory.
9. For the first accepted post-migration mutation only, publish an immutable authenticated `fs/CUTOVER_RECEIPT` containing the exact committed HEAD, then publish the matching immutable `fs/CUTOVER_COMPLETE` marker. These are recovery records after the irreversible HEAD publication, not an earlier authority pointer.
10. Release the commit lock and emit runtime invalidation for the newly committed generation.

Create, write, patch, move, trash, restore, and message append all commit through this protocol. A move changes the catalog mapping without rewriting an unchanged body. Trash publishes a recoverable trash entry/tombstone in the next catalog; restore publishes a new live mapping without pretending retained history vanished. If the process crashes before step 8 publishes `fs/HEAD`, the prior catalog remains authoritative and newly written objects/catalogs are recoverable garbage. If the first-cutover HEAD commits but either post-HEAD marker is interrupted, startup re-reads the mixed state under the Core-wide lock, authenticates the marked HEAD/catalog, and completes only its exact receipt and completion marker. A marker-finalization I/O error reports a successful canonical commit with recovery pending; retry does not pretend the already-published HEAD rolled back. Compatibility recovery may finish an exact authenticated receipt-only state written by an earlier receipt-before-HEAD build, but new writers never make the receipt the irreversible event. If invalidation fails after `fs/HEAD` publication, startup sees the higher committed generation and reconciles it. Garbage collection is implemented only by the separately gated `PCF-010` maintenance slice and cannot run until older retained catalog generations no longer reference an object.

`CoreFS generation` means the generation named by `fs/HEAD`; it is not inferred from filesystem modification time or an eventually delivered event.

#### V1 catalog scale envelope

V1 catalogs are complete immutable snapshots, not deltas, persistent trees, or family shards. The supported planning/benchmark envelope is:

- up to 25,000 live catalog entries
- up to 16 MiB serialized encrypted-catalog plaintext before encryption
- message segmentation sized so ordinary long-running conversation history remains well below the object bound
- catalog publication benchmarked before cutover with targets of p95 <= 100 ms at 5,000 entries and p95 <= 250 ms at the 25,000-entry/16-MiB envelope on the reference profile below; failure blocks the full-snapshot design from shipping unchanged

Reference benchmark profile and fixture:

- Windows 11 x64, local NTFS volume, release/optimized build, production Argon2id/AES-GCM/serialization code
- at least 4 physical CPU cores, 16 GiB RAM, and an internal NVMe SSD whose separately recorded 4 KiB durable-write/fsync p95 is <= 5 ms
- no OneDrive-synchronized, network, removable, RAM-disk, or write-cache-without-durability target
- deterministic fixture matrix: 5,000 live entries plus 500 tombstones; 25,000 live entries plus 2,500 tombstones that must serialize at or below 16 MiB; and, when that maximum-live fixture serializes below 16 MiB, a separate 16-MiB serialized-catalog fixture with no more than 25,000 live entries
- each sample measures the complete commit path: serialize, encrypt, temporary-file write, durable flush, atomic rename, directory durability operation supported by the platform, `fs/HEAD` write/flush, and lock hold time
- 30 warm-up commits followed by at least 200 measured commits per fixture; report p50/p95/p99, bytes written, live/tombstone/total counts, and hardware/filesystem metadata in a checked benchmark artifact

The 5,000-live fixture must meet p95 <= 100 ms. Both the 25,000-live fixture and the separate 16-MiB fixture, when required, must meet p95 <= 250 ms. If the maximum-live fixture exceeds 16 MiB, the declared support envelope fails and the design must be revised before cutover; the benchmark must not measure an unsupported oversized catalog or skip the max-size gate. Tombstones do not reduce the advertised live-entry capacity.

These are V1 support targets, not a reason to reject or delete content at runtime. Approaching the envelope produces health/capacity warnings and a documented export path. A later version may introduce family-sharded catalogs, a persistent authenticated tree, or deltas with periodic compaction, but V1 cannot silently substitute one of those structures because it would change recovery and atomicity semantics.

### 7.5 Stable References

Cross-content references use stable CoreFS URIs, never machine paths:

```text
corefs://object/<object-id>
corefs://thread/<thread-id>/message/<message-id>
```

Memory evidence stores these opaque references plus a source revision/content hash. Soul memory does not copy an entire diary entry or transcript merely to preserve provenance.

## 8. CoreFS Logical Filesystem Service

The public architectural name is **Core Filesystem** and the implementation/API is **CoreFS**. V1 is a virtual encrypted filesystem accessed through ANIMA: it does not yet provide Finder/Explorer/editor mounting, host file watching, arbitrary partial writes, symlinks, or full POSIX semantics. A future authenticated mount adapter may expose the same logical paths without changing the canonical object/catalog format.

### 8.1 Public Operations

The service exposes coding-agent-style operations over logical paths:

```text
list(path, cursor?, limit?)
walk(path, cursor?, limit?, max_depth?)
glob(pattern, cursor?, limit?)
grep(query, path?, mode?, cursor?, limit?)
search(query, path?, mode?, cursor?, limit?)
read(path, offset?, limit?)
stat(path)
mkdir(path, role?, owner?, agent_access?, metadata?)
create(path, content, metadata?)
write(path, content, expected_revision)
apply_patch(patch, expected_revisions)
move(source, destination, expected_revision)
trash(path, expected_revision)
restore(trash_id, destination?, expected_revision?)
```

Agent tools use a `corefs_` prefix, and desktop APIs and agent tools call the same underlying service. Host tools retain names such as `read_file`, `grep`, and `apply_patch`; CoreFS tools are `corefs_read`, `corefs_grep`, and `corefs_apply_patch`. `corefs://` is never accepted as a host path, host paths are never accepted by CoreFS, and routing is never inferred from a string.

The shared V1 production limits are defaults adapted from the audited Codex filesystem implementation: 1 MiB streaming read chunks, walk depth 64, at most 10,000 visited directories, at most 50,000 entries, and at most 4 MiB in one model-visible response. Pagination may make a larger collection traversable, but no call may silently exceed these limits. CoreFS adds its own per-format and total-object limits.

CoreFS path comparison is portable and backend-independent: each component is validated and stored in Unicode NFC, display spelling is preserved, and sibling lookup is case-sensitive. Duplicate NFC names are rejected. HostFS retains the host operating system's path/case behavior; the shared library cannot impose CoreFS semantics on it. A future mount adapter must translate or fail explicitly rather than creating an alias.

`grep` streams only declared text content by default and returns stable ID/path, revision, line number, byte offset, and a bounded excerpt. It supports literal and Rust linear-time regex modes; unsupported backreferences/look-around fail explicitly. Binary/invalid-text content is skipped with a typed reason unless the caller selects an allowed byte mode. Cancellation, deadlines, maximum files/matches/line bytes, response bytes, and continuation cursors are enforced inside the engine rather than after results accumulate.

`search` is the faster Runtime-index-backed operation and may provide lexical/semantic ranking with an explicit readiness/generation status. `grep` is the bounded authoritative CoreFS scan and does not require PostgreSQL indexes to be complete. Neither silently substitutes for the other.

### 8.2 Safety Boundary

The service must:

- normalize paths and reject absolute paths, `..`, NULs, reserved names, and ambiguous Unicode normalization
- never follow host symlinks or junctions from the object store
- enforce content-family permissions and size limits
- resolve the authenticated principal and inherited folder policy before content access; explicit deny wins
- require a user-approved, folder-scoped read/write/manage capability for each client extension
- revoke every CoreFS handle, stream, iterator, and derived plaintext view when the Core locks
- validate decoded schema before returning content
- sanitize HTML with a versioned content-family allowlist before canonical publication; for Diary, decode/validate/extract permitted inline `data:` media and replace it with CoreFS URIs in the same preflighted publication
- sanitize canonical HTML again at the presentation boundary as defense in depth
- require optimistic revision checks for mutation
- bound read/search output for context-window safety
- record runtime audit events without copying private body content into logs

The agent does not receive raw encryption keys or physical object paths. Normal deletion creates a recoverable encrypted trash record. `PCF-010` adds a separate maintenance operation `purge(trash_id, expected_trash_revision, confirmation)` with all of these requirements:

- the authenticated principal is the user; ANIMA and client principals are rejected regardless of `manage`
- a recent passphrase or recovery-phrase reauthentication issues a one-use confirmation challenge bound to Core ID, trash ID, revision, descendant inventory hash, restore-window waiver, and known-backup warning set
- the target is already in trash and unchanged; a live path/object ID, stale revision, unconfirmed recursive folder inventory, or pinned transfer/rotation/migration is rejected
- maintenance publishes a current generation without the trashed item, then prunes every older local catalog/object revision referencing it only after the user waives those restore points; it proves zero local wrapped Object-DEK references before retiring key material
- the result states that SSD wear-leveling, exported archives, and external backups may still retain bytes

Generic garbage collection cannot infer purge intent, and normal `trash` never becomes permanent because a time threshold elapsed.

### 8.3 Atomic Mutation Protocol

1. Parse the requested operation or multi-file patch into a typed mutation plan without writing.
2. Resolve every logical path/stable ID and preflight path validity, principal policy, expected revisions, target collisions, content formats, and limits.
3. Prepare all resulting encrypted immutable object revisions outside the Core-wide lock.
4. Acquire the Core-wide lock, reload `fs/HEAD`, and revalidate every precondition against the latest catalog.
5. Publish exactly one next catalog generation containing the complete plan, or publish none. A CoreFS multi-file patch never exposes the partial-success behavior allowed by some HostFS tools.
6. Emit an index invalidation event carrying the committed catalog generation.
7. Return each changed stable ID/revision/hash plus the catalog generation and `atomic: true`.

If runtime invalidation fails, the write remains successful. PostgreSQL's last indexed generation remains behind `fs/HEAD`, so startup or periodic reconciliation deterministically detects the gap.

## 9. Runtime PostgreSQL Boundary

### 9.1 Runtime Remains Responsible For

- active agent runs, steps, tool calls, and workflow checkpoints; persisted sensitive payloads are sealed as described below
- working context, current emotion, session notes, and temporary intentions in memory or sealed form when crash durability is required
- memory candidates, pending Soul operations, promotion journal, access logs, and feedback; content-bearing candidate/operation payloads are sealed
- document processing jobs, safe extraction state, and background tasks; decrypted chunks/OCR/source spans exist only in unlock-scoped memory
- safe index catalog, reconciliation checkpoints, keyed blind-search tokens, and unlock-scoped in-memory search/embedding structures
- health, progress, retry, and operational telemetry

### 9.2 Runtime Must Not Be Authoritative For

- thread existence or user-visible thread metadata
- canonical message bodies
- diary, note, task, gallery, or attachment content
- portable settings
- durable Soul state

Runtime rows reference Core object IDs and revisions. Persistent rows may cache only safe metadata, blind tokens, or explicitly sealed crash-durable operational payloads. Decrypted derived bodies remain bounded in memory, and losing Runtime must be recoverable by following the committed catalog and re-running allowed derivations.

### 9.3 Runtime Privacy Constraint

V1 makes one hard rule: **decrypted CoreFS search data exists only in process memory while the Core is unlocked.** Persistent PostgreSQL and machine-local disk must not contain CoreFS bodies, titles, previews, plaintext chunks, FTS lexemes, OCR text, or semantic embeddings.

V1 separates:

- **persistent safe catalog**: opaque IDs, hashes, revisions, status, index versions, and non-sensitive operational fields
- **persistent keyed blind index**: optional HMAC tokens over normalized exact terms/n-grams, keyed from an unlock-only search subkey; PostgreSQL can return candidates but cannot recover terms without the key. Equality/frequency leakage must be documented and tested.
- **persistent sealed operational payloads**: only when crash durability is necessary (for example a pending Soul operation), authenticated ciphertext under `HKDF-SHA256(SQLCipher Soul key, salt=local-instance-id, info="anima-runtime-seal-v1")`; the sealing key is derived after unlock, never persisted, bound to row type/ID/owner in AAD, and cleared on lock. Rows expose only minimal routing/status metadata, and a transferred Core intentionally cannot decrypt another instance's abandoned Runtime rows.
- **unlock-scoped searchable state**: decrypted text/ranking structures and semantic vectors held only in process memory and destroyed on lock or process exit

Exact/term search may use the blind index to narrow candidates, then decrypt and verify matches in memory. Semantic search rebuilds progressively after each unlock in V1. Persisting application-encrypted search snapshots or encrypted PostgreSQL storage is deferred and requires a separate approved security design.

Cutover creates a fresh runtime data directory outside the Core rather than reusing PostgreSQL pages that previously held plaintext messages, previews, chunks, or embeddings.

## 10. Startup, Restart, and Transfer Indexing

### 10.1 Readiness State Machine

```text
locked
  -> opening_core
  -> validating_core
  -> catalog_loading
  -> catalog_ready
  -> text_indexing
  -> semantic_indexing
  -> ready
```

Failure is per phase/family when possible. A gallery embedding failure must not make diary navigation unavailable.

Published readiness includes:

- state and active phase
- objects discovered/processed/failed
- bytes processed when known
- current content family
- whether navigation, exact search, text search, and semantic search are available
- retryability and last error summary

Readiness is reported both globally and per content family. `catalog_ready` may publish with quarantined corrupt/unsupported objects when every unaffected committed catalog entry has been classified. In that case:

- the global state is `catalog_ready_degraded`, not silently healthy
- each affected family reports `ready_degraded` with unavailable object IDs/counts
- unaffected objects in the same family remain listable and readable
- duplicate logical paths are an integrity conflict for only the colliding entries; neither wins silently
- a catalog that cannot authenticate or whose committed mapping cannot be parsed blocks catalog readiness because no authoritative inventory can be established
- an individual object that fails authentication does not block unrelated content after it is quarantined and surfaced

### 10.2 Warm Restart on the Same Machine

After unlock:

1. Start/migrate the local runtime database outside the Core.
2. Compare `core_id`, the generation/hash in `fs/HEAD`, per-object revision/hash, and index schema versions with runtime checkpoints.
3. Reuse valid safe catalog and blind-token entries; decrypted text/semantic indexes are rebuilt after every unlock.
4. Reconcile only new, changed, deleted, or previously failed objects.
5. Publish `catalog_ready` as soon as the minimal inventory is consistent.
6. Continue deeper text/semantic work in the background.

### 10.3 New Machine or Deleted Runtime

After unlock:

1. Create an empty runtime for `core_id`.
2. Read `fs/HEAD`, authenticate/decrypt the named catalog generation, and enumerate only its live mappings.
3. Authenticate and index catalog-referenced objects in deterministic batches; never infer live content from unreferenced physical objects.
4. Publish `catalog_ready`; enable list/read/write operations.
5. Build searchable text indexes by content-family priority.
6. Build embeddings and other expensive indexes last.

A physical object-store scan is reserved for explicit recovery/doctor and garbage collection. It can identify orphan revisions and objects retained by older catalogs, but it cannot add them to the live filesystem without an explicit recovery decision and new catalog commit.

Recommended priority:

1. preferences and thread/diary/note metadata
2. recent thread messages and recent diary/notes
3. remaining text content
4. gallery metadata and OCR-derived text
5. embeddings and low-priority historical enrichment

### 10.4 Long-Running Indexing

- Indexing uses bounded batches and records checkpoints outside the Core.
- Cancellation or server shutdown completes the active object or safely abandons it; canonical files are unchanged.
- Safe catalog/blind-token work may resume from a valid checkpoint or reprocess a batch idempotently. Because decrypted text/semantic indexes are memory-only in V1, a process restart rebuilds those indexes from the committed catalog; pause/resume without process exit may retain the in-memory work.
- The UI never waits for a single unbounded future. It receives progress events and readiness changes.
- A configurable startup time budget controls when the system yields from foreground catalog work to background indexing.
- Search results identify partial coverage while an index family is incomplete.
- Users can retry one failed family without rebuilding unrelated families.

### 10.5 Live Updates

All writes through CoreFS emit an invalidation event. A reconciler also periodically compares the committed `fs/HEAD` generation with PostgreSQL checkpoints so missed events cannot leave the index permanently stale. Direct physical edits are unsupported because files are encrypted, but copied/replaced Core directories are detected by `core_id`, catalog generation, and catalog hash.

### 10.6 Local ANIMA CORE Transfer and Snapshot Consistency

A recursive copy of a live `.anima/` directory is not guaranteed to be consistent across SQLCipher, `fs/HEAD`, immutable objects, and recovery metadata. Supported transfer modes are:

1. **Cold copy**: stop the server or explicitly lock/close the Core, then copy `.anima/`.
2. **Export ANIMA CORE**: while running, enter a short canonical-write barrier; drain/record pending consolidation according to product policy; create a verified bounded Soul checkpoint; capture and pin the committed CoreFS catalog generation; release the barrier; then stream the selected reachable records to a local destination and atomically publish the verified artifact.

The UI must never instruct users to drag-copy a live Core. A full snapshot records the captured `(soulGeneration, filesystemGeneration)` pair so destination startup can verify that it received one coherent point-in-time package. Runtime PostgreSQL is never included. The selected catalog and objects remain pinned against GC until export completes or cancels.

#### 10.6.1 Naming and payload kinds

The product is **animaOS**. **ANIMA CORE** names the portable subsystem and its local export family:

| Payload kind | Default filename | Contents | Restore behavior |
|---|---|---|---|
| `full` | `anima-core-<timestamp>.anima` | manifest, active Soul, committed CoreFS catalog/reachable objects, required password/recovery keyslots and recovery metadata | normal coherent restore |
| `soul` | `anima-core-soul-<timestamp>.anima` | Soul plus its bootstrap/keyslot/recovery subset; no FRK or CoreFS bytes | unlock identity/cognition in explicit `filesystem_missing` degraded mode; CoreFS-dependent features remain unavailable; explicit recovery action may create a new empty CoreFS |
| `fs` | `anima-core-fs-<timestamp>.anima` | `fs/HEAD`, committed/reachable catalogs and objects, FRK keyslots, and the minimum non-private identity/generation metadata needed for verification | authenticated browse/export through animaOS recovery UI only; no agent startup or Soul attachment in V1 |

All kinds use `anima_core_v2`; the payload kind is authenticated metadata, not a filename inference. Soul has no hard database foreign keys into CoreFS. Soul provenance references use stable `corefs://` URIs plus revision/content hashes and remain inspectable as unavailable provenance when CoreFS is missing. CoreFS-only recovery never starts the agent runtime, claims identity continuity, or attaches to a Soul in V1. A later reattachment design must prove Core/owner lineage, define content conflicts, and atomically publish a coherent generation pair.

#### 10.6.2 Streaming container

`anima_core_v2` is a custom streaming container, not ZIP and not a mounted filesystem. Its physical sequence is:

1. fixed header with the exact fields defined below
2. encrypted authenticated artifact manifest repeating the header identity/volume fields and declaring Core/owner IDs, captured generations, inventory, and expected byte counts
3. ordered typed records for the selected Soul/keyslot/recovery files and reachable encrypted catalogs/objects
4. authenticated footer containing the complete record index and whole-artifact commitment

Every implementation uses this exact derivation:

```text
argon = Argon2id(passphrase, salt=kdfSalt, time=4, memory=131072 KiB, parallelism=4, outputLength=32)
archiveKey = HKDF-SHA256(ikm=argon, salt=None, info="anima-core-archive-v2", outputLength=32)
```

The exact fixed-header field order is `magic`, `formatVersion`, `headerLength`, `cipherId`, `kdfId`, `kdfProfileId`, `kdfTimeCost`, `kdfMemoryKiB`, `kdfParallelism`, `kdfSalt[32]`, `archiveId`, `volumeSetId`, `payloadKind`, `volumeMode`, `declaredVolumeCount`, `chunkLimitBytes`, and `noncePrefix[4]`. Before invoking Argon2, import validates magic/version/header length, requires the registered V2 cipher/KDF/profile and exact costs above, requires the 32-byte salt and 8-MiB chunk limit, validates enum/count/ID encodings, and rejects unknown or out-of-range values. Unsupported profiles fail before expensive allocation. The encrypted manifest repeats `archiveId`, `volumeSetId`, `payloadKind`, `volumeMode`, and `declaredVolumeCount`; every repeated value must exactly equal the fixed header. `headerHash` is SHA-256 over the exact serialized fixed header and is authenticated by the encrypted manifest and every chunk.

The exporter first makes a bounded pre-hash pass over each pinned stable record and defines `recordHash` as SHA-256 of the exact pre-archive record bytes, then makes the encryption pass. Every chunk uses the normative AAD tuple from the security design without abbreviation: `headerHash`, `archiveId`, `volumeSetId`, `payloadKind`, `recordType`, `recordPath`, `recordOrdinal`, `recordHash`, `chunkIndex`, `chunkCount`, `plaintextOffset`, `plaintextLength`, `ciphertextLength`, `finalFlag`, and `volumeOrdinal`. Archive nonces are a fresh random 32-bit archive prefix plus one monotonically increasing 64-bit global chunk ordinal that never resets across records or volumes; interruption restart creates a new archive key/prefix/sequence. Existing SQLCipher and CoreFS encryption remains intact underneath.

Single-file artifacts use `volumeSetId=archiveId` and `volumeOrdinal=0`; multipart ordinals start at 1. Counts, offsets, lengths, and final flags are computed from the stable preflighted record length before encryption and verified on import.

Each source or ciphertext buffer is at most 8 MiB. Aggregate export/import streaming working memory is at most 32 MiB, excluding the fixed 128-MiB Argon2 workspace and fixed runtime/library overhead. Inventory/footer entries are incrementally hashed and, if disk-spooled, remain encrypted/authenticated under `archiveKey` and are deleted with the partial artifact on failure. Tests assert both per-buffer and aggregate transfer-memory ceilings with an artifact larger than RAM.

Single-file export writes `<final-name>.partial`, flushes progress durably, writes and reopens/verifies the footer and complete reachable inventory, fsyncs the file and parent directory, then atomically renames to the final `.anima` name. Cancellation, disconnect, or process failure leaves the live Core unchanged and a visibly incomplete artifact that import rejects.

Import preflights the activation destination as well as the source: it requires same-volume staging capacity for the complete restored Core plus filesystem/safety margin while retaining any existing Core untouched. It authenticates each record before publishing bytes into a path-safe sibling staging Core. A new-machine import fsyncs and renames that staging directory to the selected final path. Replacing an existing Core never overwrites it in place: after locking both paths, import fsyncs the staged Core and atomically swaps a machine-local active-Core registry pointer to the new directory, retains the old Core as rollback material, and records completion before releasing the lock. Startup recovers an interrupted pointer transaction by selecting only a fully verified directory named by the last authenticated registry generation.

#### 10.6.3 Hard drives and removable media

Before export, ANIMA probes the selected local destination for writable status, available bytes, maximum single-file size, path/name support, and removable-media disconnect behavior. The estimate includes selected reachable bytes, archive framing, filesystem allocation margin, and temporary/footer overhead. Encrypted media is treated as incompressible for capacity planning.

When the destination supports the final size, export writes one `.anima` file. When it does not support one large file (for example FAT32 above its approximately 4-GiB limit), the UI offers an authenticated multipart directory only if total capacity is sufficient:

```text
anima-core-<timestamp>/
  core.anima                    # small controller/header and final volume-set commitment
  volume-0001.anima-part        # each bounded below the detected single-file limit
  volume-0002.anima-part
  ...
```

Every volume binds the archive/volume-set ID, payload kind, ordinal, declared count, length, and hash. Missing, duplicated, reordered, mixed-set, truncated, or appended volumes fail closed. The default part ceiling is 2 GiB unless a smaller detected limit requires less. Import selects `core.anima`, verifies the complete volume set before activation, and may stream directly from the removable device into a local staging Core. The live `.anima/` directory is not run from removable media.

Multipart export creates a same-destination sibling `<set-name>.partial/`. Each volume is written as `volume-####.anima-part.partial`, fsynced, reopened/verified, and renamed to its final part name inside that directory. After every declared part is durable, the exporter writes and fsyncs `core.anima.partial`, whose authenticated controller commits the ordered complete volume inventory, then renames it to `core.anima` as the internal completion marker. Finally it fsyncs the directory and atomically renames `<set-name>.partial/` to the final set directory. If the destination cannot provide same-filesystem atomic rename semantics, multipart export is rejected before writing. Failure injection covers every part flush/rename, controller write/rename, directory flush/rename, disconnect, and cleanup boundary.

## 11. Agent Interaction Model

ANIMA interacts with CoreFS like a coding agent interacts with a repository:

1. list, walk, or glob to discover relevant logical files
2. grep or indexed search to narrow candidates
3. read bounded regions or structured records
4. write or apply a revision-checked narrow patch
5. create/mkdir/move/trash/restore through explicit tools

Feature-specific tools may remain as convenience wrappers, but they must delegate to CoreFS. For example, `create_diary_entry` can construct sanitized HTML plus typed metadata and call `CoreFS.create`; it cannot write a separate diary database row.

Search and read results identify logical path, Core object ID, revision, and content hash so later edits are race-safe and memory provenance is stable.

`anima-file-tools` supplies the common contracts, limits, typed patch parser/planner, and backend-independent conformance suite. Animus instantiates them with HostFS; CoreFS instantiates them with its encrypted catalog backend. Backend-specific authorization and transaction semantics stay visible in capability/result metadata. The HostFS backend may report best-effort multi-file mutation where the operating system cannot guarantee a transaction; CoreFS always reports atomic catalog-generation publication.

The initial implementation selectively adapts proven patterns from the local Apache-2.0 Codex checkout at audited commit `9e552e9d15ba52bed7077d5357f3e18e330f8f38` (2026-07-11): its separate filesystem/apply-patch crates, streaming file reads, bounded directory walk, typed patch changes, parser scenario tests, head/tail output bounding, permission deny precedence, and tool spec/handler/runtime separation. animaOS does not depend on that checkout at runtime. If `anima-file-tools` incorporates adapted implementation, its Cargo package is Apache-2.0 rather than silently inheriting MIT-only workspace metadata; adapted source retains Apache-2.0 headers. `THIRD_PARTY_NOTICES.md` records every upstream-to-local adapted file, commit, modifications, and license; the repository/distribution includes the complete Apache-2.0 text plus the applicable upstream Codex NOTICE. A deterministic attribution/dependency check rejects unlisted Apache-marked files, missing license/NOTICE artifacts, external path dependencies, and local-Codex path references. CI builds/tests a clean animaOS checkout where the sibling Codex checkout is absent. Codex host-path URI/sandbox code and partial-success patch semantics are not copied into CoreFS.

## 12. Desktop and API Integration

- Routes require an unlocked Core session instead of a Soul SQLAlchemy session for app content, then authenticate and resolve the actual user, ANIMA, or installed-client principal before evaluating that principal's folder capability. Owner scope is required only for user-only operations such as policy, ownership, grants, reserved-role binding, purge, and key retirement.
- Domain services parse/render logical formats and remain independent from encryption and physical storage.
- The desktop continues to receive typed diary/thread/gallery responses; it need not parse encryption envelopes.
- The generic CoreFS API can support future file-oriented surfaces, but unrestricted arbitrary writes are not required for the first migration.
- Journal, Gallery, Notes, Conversations, and future features resolve their root by stable folder role, then stable folder ID; display path is never the source of truth.
- A client/mod extension presents a verified installed-package identity plus unique client ID, requested namespaced roles/metadata schemas, and folder-scoped capabilities. The user approves read/write/manage scope per folder; revocation takes effect immediately and the device-local grant survives restart on that machine.
- Clients may create descendants and namespaced metadata only inside approved scope. They may not claim reserved roles, access sibling folders, edit policy/grants, or persist a reusable decrypted handle.
- Client grants are authenticated machine-local capability records bound to Core ID, local instance, verified package identity, folder ID, scope, and grant generation. Reusable bearer material is kept in process memory or the OS credential store. Transfer preserves client-authored folders/data but never silently authorizes a destination executable; the user must install and approve it again.
- Readiness/progress is exposed through health plus SSE/WebSocket events used by startup and settings UI.
- Device settings stay in platform application data; portable preferences use CoreFS.
- Provider secrets move to the OS credential store and are not copied with the Core.
- Local unlock changes from querying the SQLCipher `users` table to unwrapping manifest keyslots and then reading the encrypted account-profile object.

### 12.1 Client Identity and Capability Trust

The server never accepts a caller-supplied client ID as proof of identity. The device-local capability broker derives a principal from an installed package record:

- a canonical manifest supplies a reverse-DNS package ID, display name, version, requested roles/scopes, and payload digest
- the broker computes the payload digest itself from the installed package and assigns the local installation record; the client cannot choose or rewrite the grant principal
- a signed package additionally proves publisher identity through a signature over the canonical manifest and digest; the user explicitly trusts that publisher key on first approval
- an unsigned/local-development package is bound to its exact digest and receives no publisher continuity
- the portable role namespace uses the manifest package ID, but authorization uses the broker-issued local installation principal; possession of the package ID alone grants nothing
- two different publisher identities or unsigned payloads claiming the same package ID cannot run concurrently against that namespace; activation stops for explicit user resolution and never merges grants automatically
- V1 requires reapproval after every payload-digest change, including an update signed by the same publisher. A later signed-update policy may relax this only through a separate design.

After approval, the broker issues a short-lived, audience-scoped, generation-bound capability to the launched process over the authenticated local channel. Restart may renew it only from the same verified installation record and unrevoked grant. Lock, uninstall, digest mismatch, grant-generation change, or package-ID collision invalidates issuance and all open handles. Tests must attempt client-ID spoofing, manifest substitution, payload replacement, signature/publisher mismatch, namespace collision, stale-token replay, and update without reapproval.

### 12.2 Current Settings Classification

The implementation plan must maintain a checked inventory of every persisted desktop/server key. The initial classification is:

| Current state/key family | Destination | Reason |
|---|---|---|
| `anima-theme`, `anima-background-config`, background media | portable preferences plus Core attachment object | user-chosen appearance should follow the Core |
| `anima-translate-lang`, clock format, ASCII rendering preferences | portable preferences | user interaction preference, not machine capability |
| dashboard node positions/closed nodes and durable navigation/layout choices | portable preferences | user-arranged filesystem |
| unsaved Journal draft keys | encrypted draft Core objects | private authored content must not remain plaintext localStorage |
| BGM mute/player preference when using bundled media | portable preferences | user interaction preference |
| custom local BGM/media path | device-local config; portable media requires explicit Core import | host paths do not transfer |
| nav/sidebar/agent-editor collapsed state | device-local UI state | transient viewport state, not portable content |
| show-trace, DB viewer, query drafts | device-local developer state | machine/debug specific |
| provider, model, extraction model, Ollama/vLLM URL, local model paths | device-local runtime config | availability and performance are machine specific |
| cloud-provider consent toggle | device-local security/runtime choice | must be re-evaluated on a new machine |
| provider API keys, connector tokens, daemon control token | OS credential store | secret, non-portable by default |
| provider key hint/last-four display | device-local derived state | not canonical and can be regenerated |
| mod URL and daemon URL/ports | device-local runtime config | machine topology specific |
| remembered login username | optional device-local convenience only | username is encrypted in account profile and not needed for key unwrap |
| unlock tokens, greeting cache, setup recovery phrase state | session/process state | ephemeral and never migrated as canonical data |

Before cutover, an automated/static inventory test must enumerate browser storage keys, persisted server settings, Tauri app-data files, and credential calls. Any persisted key absent from the approved classification fails the migration gate; it cannot default silently to portable or device-local.

## 13. Migration Design

### 13.1 Migration Principles

- copy, verify, then flip; never rewrite the only canonical copy in place
- preserve stable user-visible IDs where externally referenced
- record source table/row/archive references in a migration journal outside canonical content
- make each converter idempotent
- do not drop legacy tables or files in the same release that first performs cutover
- support restart after any batch
- compare counts and deterministic hashes before activating the new manifest version

### 13.2 Source Families

| Current source | Target |
|---|---|
| SQLCipher `users` | encrypted account-profile object; opaque owner ID retained in manifest |
| SQLCipher `user_keys` | Soul-domain entries migrate to a Soul-internal `soul_keyslots` representation decoupled from the application account row; obsolete app-auth key rows are removed after verification |
| legacy `users/<id>/anima.db` | checkpoint, close, copy-verify-flip into the single-owner `.anima/soul/soul.db`; retain the old encrypted copy only until the authenticated first-write cutover marker |
| manifest wrapped/recovery keys | password/recovery keyslots for the SQLCipher Soul key and Filesystem Root Key, with active/decrypt-only versions |
| plaintext manifest `user_index` | removed; optional remembered username remains device-local only |
| SQLCipher `diary_folders` | first-class folder entries preserving stable IDs, hierarchy, ordering, names, empty folders, and the unique `core.journal` role |
| SQLCipher `diary_entries`, `diary_attachments` | sanitized diary HTML objects plus encrypted cover, attachment, and extracted-inline-media objects linked by CoreFS URIs; plaintext legacy bodies are escaped into HTML paragraphs and no canonical `data:` URL remains after migration |
| SQLCipher `tasks` | task JSON objects |
| legacy SQLCipher `agent_threads/messages/runs/steps` | canonical thread/message objects where still populated; runs/steps remain runtime only if operational |
| PostgreSQL `runtime_threads`, `runtime_messages` | canonical thread metadata and message segments |
| encrypted transcript JSONL | canonical historical message segments, deduplicated against runtime messages |
| PostgreSQL image assets/annotations/links | gallery/attachment objects and CoreFS URI references |
| PostgreSQL `runtime_documents` and original file-backed uploads/source artifacts | canonical attachment/document/source objects when original user-owned bytes or imported text/Markdown exist; web capture preserves both original raw HTML and its normalized structured snapshot as immutable encrypted source revisions so exact rebuild and future offline re-extraction do not require the network; safe ingestion status remains Runtime |
| PostgreSQL document chunks, OCR text, source spans, contextual blurbs, compiled concept title/description/body/frontmatter, citation quote text, previews, and workflow checkpoints | plaintext derivations rebuild into unlock-scoped process memory; PostgreSQL retains only safe opaque/hash/locator/status/checkpoint fields |
| desktop localStorage/Tauri app-data/server settings | migrate according to Section 12.2; no unclassified key may cross cutover |
| presence/integration/config/link tables | explicit row/field inventory into Soul, portable preferences, device config, OS secrets, or removed derived state |
| Soul-side vector caches (`memory_vectors`, all `embedding_json`/`embedding_checksum` columns, and experience-cluster centroids) | remove after the observation gate; rebuild unlock-scoped vectors/indexes from retained Soul text/state into process memory only |

### 13.3 Conversation Merge

Conversation migration is the highest-risk source merge because active PostgreSQL messages and archived JSONL may overlap.

Required deduplication identity:

- stable message ID when available
- otherwise thread ID + sequence + role + normalized timestamp + content hash

The converter writes ordered immutable message segments, validates continuity, and reports gaps/duplicates. It must not silently choose between conflicting bodies with the same identity.

#### Canonical Conversation Projection

A canonical message preserves the user-visible conversation record:

- stable message ID, thread ID, sequence, role, and timestamps
- user-authored content blocks
- the final assistant content shown to the user
- edit/delete state when supported
- CoreFS URIs for visible images, documents, and other attachments
- durable presentation metadata required to render those visible blocks

The canonical projection excludes operational execution data:

- hidden system prompts and injected memory/context blocks
- assistant tool-call wrapper rows
- tool arguments and raw tool-result rows, except a user-visible artifact/message is projected into a normal visible content block
- chain-of-thought, internal thinking, trace events, and delegated-agent scratch output
- run/step IDs except optional non-sensitive provenance needed for diagnostics
- retrieval candidates, document chunks, token counts, latency, provider payloads, and usage accounting

Current `RuntimeMessage.is_internal` behavior is a starting point, not the complete migration rule. The migration must use the same tested `canonical_message_projection` function for active PostgreSQL rows and archived transcript records. Unknown roles or content-block types are quarantined for review rather than copied or dropped silently. Attachment/image links are resolved to CoreFS URIs before the message segment is verified.

### 13.4 Cutover

1. Preflight disk space, Core unlock, source schema heads, and encryption keys.
2. Set migration state `migrating-write-frozen` and quiesce app-content writes for the initial migration snapshot.
3. Convert sources in resumable batches.
4. Verify object counts, hashes, references, decryptability, and representative API parity.
5. Write a new manifest CoreFS layout version with state `corefs-validation-readonly` atomically.
6. Rebuild runtime outside `.anima/` from the new canonical files.
7. Run smoke checks for auth, chat, diary, notes, gallery, memory, settings, and health.
8. Keep CoreFS writes frozen while the operator/user accepts or rejects the verified cutover. Rejection restores `legacy-authoritative` state.
9. If accepted, set state `corefs-approved-pending-first-write` and enable writes. Rollback remains available while no mutation has committed.
10. The first successful CoreFS mutation publishes an encrypted authenticated catalog containing `legacyRollbackDisabled=true` and a stable cutover epoch, then points `fs/HEAD` at that catalog. Publication of that `fs/HEAD` is the single irreversible event. After the HEAD is durable, publish the exact authenticated `CUTOVER_RECEIPT` and matching `CUTOVER_COMPLETE`; interruption of either marker is recovery-pending success, never rollback of the committed HEAD.
11. After the marked catalog/`fs/HEAD` commits, update manifest state to `corefs-authoritative-forward-only`. If a crash occurs between steps 10 and 11, startup re-reads under the Core-wide lock, follows `fs/HEAD`, authenticates the catalog marker, completes any missing post-HEAD cutover markers, and finalizes the manifest state; it never re-enables legacy rollback.
12. Retain source databases/files as read-only recovery material until an explicit later cleanup release.

Rollback is supported in `migrating-write-frozen`, `corefs-validation-readonly`, and `corefs-approved-pending-first-write`, provided the catalog referenced by committed `fs/HEAD` does not contain the authenticated cutover marker. Rollback restores the previous manifest/layout pointer and leaves newly created encrypted objects unreferenced for later cleanup. Once the marked first mutation commits, automatic legacy rollback is permanently disabled; recovery is forward-only using CoreFS repair, verified backup restore, or export/import. This avoids maintaining a second mutation compatibility system.

## 14. Failure Handling

| Failure | Required behavior |
|---|---|
| Committed catalog authentication/parse fails | block catalog readiness and offer recovery from a retained prior catalog generation; never guess an inventory |
| Object authentication fails | quarantine object, mark its family and global catalog `ready_degraded`, report opaque identity, continue unaffected entries |
| Object schema version unsupported | keep object untouched, mark only affected entries/family degraded, continue supported objects |
| Canonical write succeeds, indexing fails | return write success with index-stale state and retry |
| Indexer crashes | preserve checkpoint; resume/reconcile idempotently |
| Runtime DB missing/corrupt | recreate outside Core and rebuild |
| User requests live transfer | take the Section 10.6 write-barrier snapshot; do not recursively copy the changing directory |
| Destination lacks total capacity or writable support | reject before writing and report required versus available bytes without changing canonical state |
| Destination cannot hold one archive file | offer the Section 10.6.3 authenticated multipart form when total capacity is sufficient; otherwise reject before writing |
| Export is interrupted or removable media disconnects | leave/reject `.partial` or incomplete volume set; unpin the snapshot only after cancellation cleanup; never modify the live Core |
| Import lacks same-volume staging capacity or activation support | reject before extraction and report required bytes/activation constraint; preserve the existing Core and registry pointer |
| Import is interrupted or one archive/volume authentication fails | delete or quarantine staging bytes; preserve the existing Core and active-Core registry generation unchanged; report the failing opaque record/volume |
| Soul-only restore | enter `filesystem_missing`; allow degraded Soul identity/cognition but disable CoreFS-dependent features; do not fabricate recovered content; require explicit confirmation plus complete new FRK password/recovery wrappers before initializing an empty filesystem |
| CoreFS-only restore | enter restricted authenticated animaOS recovery/export mode; do not start ANIMA identity/runtime; V1 attachment attempts return `corefs_reattachment_not_supported` |
| Raw folder is copied while server is active | unsupported; destination validation reports an incoherent snapshot instead of promising recovery |
| Duplicate logical paths in catalog | quarantine both colliding mappings, publish degraded family state, do not choose a winner |
| Stale edit | return revision conflict with current revision; never last-write-wins silently |
| Migration interrupted | reopen migration journal and resume verified batches |
| Insufficient disk space | fail before cutover and keep legacy authority active |
| Rollback requested with no committed cutover marker | restore legacy manifest/layout authority from any allowed pre-write migration state |
| Crash or I/O failure after marked `fs/HEAD` but before cutover receipt/completion | preserve successful commit semantics, report recovery pending, then authenticate and complete the exact post-HEAD markers under the Core-wide lock; never republish a divergent HEAD |
| Crash after marked `fs/HEAD` but before manifest finalization | derive forward-only authority from authenticated `fs/HEAD`/catalog marker and finalize manifest; never roll back |
| Rollback requested after marked first CoreFS mutation | reject automatic legacy rollback and use forward repair or verified CoreFS backup restore |

## 15. Observability

Operational events may include object IDs, phases, counts, durations, revisions, and error codes. They must not include decrypted bodies, titles, diary text, message text, secrets, or raw keys.

Metrics:

- time to Core open
- time to catalog readiness
- time to full text readiness
- time to semantic readiness
- objects and bytes indexed per family
- reused versus rebuilt index entries
- failed/corrupt objects
- stale-index duration after writes
- migration counts, conflicts, and verification failures

## 16. Testing Strategy

### 16.1 CoreFS Contract

- list/walk/glob/grep/search/read/stat/mkdir/create/write/apply-patch/move/trash/restore behavior
- shared `anima-file-tools` conformance across explicit HostFS and CoreFS backends, with no path-based auto-routing
- 1-MiB stream chunks, walk-depth/directory/entry ceilings, 4-MiB response cap, pagination stability, cancellation, and lock-time stream revocation
- traversal, absolute path, Unicode, symlink/junction, and size-limit rejection
- exact NFC/case-sensitive CoreFS lookup and backend-declared HostFS path semantics
- literal/regex grep, unsupported-regex diagnostics, binary/invalid-text policy, match offsets, cancellation/deadline, and match/line/output ceilings
- optimistic revision conflicts
- multi-file patch preflight and one-generation atomic publication; no partial CoreFS success
- folder ownership/access inheritance, explicit-deny precedence, user-owned non-escalation, client capability revocation, package-identity mismatch, and destination-machine reapproval
- stable `core.journal`, `core.notes`, `core.conversations`, and `core.gallery` role resolution across rename/move/restart and reserved/client namespace collision rejection
- recoverable trash/restore and user-authorized permanent-purge boundary
- immutable object/catalog publication and `fs/HEAD` failure injection before and after every publish boundary
- orphan object recovery and retained-catalog garbage-collection safety
- envelope authentication and wrong-key behavior
- key rotation and old-key compatibility

### 16.2 Format Contracts

- Markdown frontmatter round-trip for Notes and imported text sources
- versioned diary/Note HTML sanitization boundary, including extraction and URI resolution for embedded `data:` media
- JSON schema versioning
- first-class folder entries preserving empty/custom folders, stable IDs, roles, ownership, policy, and namespaced metadata
- message-segment ordering, append, deduplication, and corruption isolation
- 256-event/1-MiB rollover boundaries, imported ordering, hash-chain gaps, and concurrent tail retry
- message create/edit/delete compare-and-swap preconditions, terminal deletion, and stale transition conflicts
- canonical conversation projection excluding internal/tool/trace rows while retaining visible attachments
- binary hash/reference integrity

### 16.3 Index Lifecycle

- empty runtime/new-machine rebuild
- warm incremental restart
- runtime deletion and rebuild
- schema-version forced rebuild
- cancellation/shutdown/resume at every phase
- partial readiness and progress events
- degraded catalog readiness for corrupt, unsupported, and duplicate-path objects
- write/index failure independence
- lock-time purge of unlock-scoped searchable state
- raw PostgreSQL/runtime-disk scans proving no portable plaintext bodies, previews, chunks, FTS lexemes, OCR text, or semantic embeddings persist
- blind-token candidate retrieval plus in-memory decrypt/verify, equality/frequency leakage documentation, and key rotation

### 16.4 Migration

- representative legacy account/keyslot/user-index, SQLCipher, PostgreSQL, transcript, runtime-document/upload, attachment, gallery, and desktop/server settings fixtures
- overlapping active/archive conversation history
- complete persisted-setting inventory and classification gate
- copy verification and rollback across all pre-cutover-marker states; forward-only behavior after the first marked CoreFS mutation publishes `fs/HEAD`
- insufficient disk and corrupt-source handling
- stable ID/reference preservation

### 16.5 End-to-End

1. Create a Core with all supported content families.
2. Chat and cause one approved memory promotion with Core provenance.
3. Cold-copy a closed `.anima/`, then stream a live full ANIMA CORE export through the write-barrier flow to a normal large-file destination.
4. Stream the same snapshot through a FAT32-like adapter into multiple authenticated volumes; inject failure at every part/controller/directory publication boundary plus removal, missing/reordered/mixed volumes, insufficient capacity, and footer corruption.
5. Round-trip `full`, `soul`, and `fs` artifacts; verify normal, degraded `filesystem_missing`, and recovery/export-only states, scoped credential replacement, and V1 rejection of every CoreFS-to-Soul attachment attempt.
6. Inject import failure before/after each staging fsync, final-directory rename, active-Core registry pointer swap, and completion record; verify the old Core remains selected unless the new verified registry generation committed.
7. Start the full restore with no runtime database, unlock, and observe staged readiness.
8. Verify navigation before semantic indexing completes.
9. Verify content hashes, search after completion, Soul identity, and memory provenance.
10. Delete runtime and repeat the rebuild.
11. Verify unlock/account profile restoration without the legacy SQLCipher `users`/`user_keys` tables or plaintext manifest username index.

## 17. Rollout Slices

The implementation plan should preserve independently testable slices:

1. foundation: manifest/layout, SQLCipher Soul keyslot, Filesystem Root Key hierarchy, per-object DEKs, envelope, and CoreFS contract
2. runtime relocation and staged index/readiness framework
3. diary and notes as the first file-backed vertical slice
4. canonical threads/messages and transcript merge
5. gallery/assets/attachments
6. tasks and portable preferences; device/secrets cleanup
7. Soul schema purity and legacy compatibility removal
8. full migration, transfer, rollback, documentation, and release validation

Each slice keeps legacy reads available until its file-backed path passes verification. No slice should require dropping legacy data to demonstrate success.

## 18. Documentation Changes Required

Implementation must update documents that currently say PostgreSQL lives inside `.anima/` or that Runtime is authoritative for active messages, including:

- `docs/thesis/whitepaper.md`
- `docs/thesis/portable-core.md`
- `docs/thesis/three-tier-architecture.md`
- `docs/architecture/README.md`
- `docs/architecture/system/anima-core-filesystem.md` (remove the planned-status warning only after the cutover acceptance gate passes)
- `docs/architecture/memory/memory-system.md`
- `docs/architecture/system/database-schema.md`
- `docs/architecture/agent/document-processing.md`
- `docs/architecture/agent/source-ingestion.md`
- `docs/prds/three-tier-architecture.md`
- relevant setup, vault, recovery, and developer workflow documentation

## 19. Acceptance Boundary

The redesign is complete only when:

- a clean schema inspection shows app-specific canonical tables are no longer active in SQLCipher
- a Core copied without runtime data restores all supported app content and Soul continuity
- deleting runtime causes rebuild, not data loss
- desktop and agent edits converge through CoreFS
- startup exposes progressive readiness and remains usable before expensive indexes finish
- locked canonical Core bytes reveal no seeded private plaintext
- legacy migration and rollback tests pass
- architecture and product documentation consistently describe Core, Soul, Core Filesystem, and Runtime
