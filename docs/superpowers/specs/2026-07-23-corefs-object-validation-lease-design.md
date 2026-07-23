# CoreFS Object Validation Lease Design

**Status:** Revised with a macOS backend on 2026-07-23; independent review and
written-spec reapproval pending

**Ticket:** PCF-002 catalog-performance architecture revision

**Parent:** PCF-000 Portable Core Filesystem

**Prior design:** `docs/superpowers/specs/2026-07-20-corefs-catalog-commit-performance-design.md`

**Independent review:** The user-approved Windows baseline passed four review/revision
rounds covering notification-name ambiguity, production link-count behavior,
unlock-session ownership, operation draining, terminal close/release races, route
authority, steady-state lease carry-forward, and process-wide resource bounds. The
first macOS review found three Important stream-lifecycle issues. A second pass
confirmed two resolved but retained the zero-ID `RootChanged` ambiguity. The
third pass confirmed the paired vnode protocol but required its chain to begin at the
absolute namespace root rather than the mounted volume root. The corrected revision
below requires re-review before planning.

## Context

PR #117 merged the first catalog-commit optimization pass. Its source-current exact
30-warm-up/200-sample reference run passes every unchanged PCF-002 catalog gate except
maximum-live commit latency:

| Fixture | Object files | Commit p50/p95/p99 | Gate | Result |
|---|---:|---:|---:|---|
| medium | 500 | `67.9843/78.9163/84.2530` ms | p95 <= 100 ms | Pass |
| maximum-live | 2,500 | `251.3036/361.1692/442.4276` ms | p95 <= 250 ms | Fail |
| serialized-limit | 0 | `126.0642/157.8677/169.2512` ms | p95 <= 250 ms | Pass |

The similarly sized catalog-only fixture is green. The remaining cost is concentrated
in `validate_prepared_revisions`: every unchanged object still performs a
capability-relative open, opened-versus-linked identity validation, regular-file and
symlink validation, link-count validation, a metadata length query, and close.

The user approved a security-equivalent revision on 2026-07-23. Full catalog snapshots,
catalog/object wire formats, public APIs, durability, recovery, rotation, benchmark
fixtures, timer boundaries, and acceptance thresholds remain unchanged. The revision
may replace repeated unchanged-object opens only when it proves behavior equivalent to
the current filesystem validation boundary.

## Decision

Add a process-local **object validation lease** for the Windows and macOS reference
paths. A valid lease combines:

1. exact existing authenticated pointer, key-identity, catalog, and object-binding cache
   authority;
2. one platform validation anchor for every validated referenced object: a retained
   file handle and captured identity on Windows, or a capability-relative file stamp on
   macOS;
3. fresh native metadata checks on every commit: handle metadata on Windows or
   non-following `fstatat` metadata on macOS; and
4. an ordered, fail-closed object-directory change monitor used to decide which paths
   require the existing full safe-open validation.

An exact clean lease replaces 2,500 repeated open/path-resolution/close cycles with
2,500 bounded metadata queries plus two ordered monitor fences. Windows queries the
retained handles. macOS queries each physical name relative to the pinned directory
descriptor with `fstatat(..., AT_SYMLINK_NOFOLLOW)` and compares it with the
safe-open-derived stamp. Any directory event invalidates the whole lease and follows
the current complete safe-open path. Any uncertainty also disables the optimization.

The lease is an optimization, never disk or cryptographic authority. It is not
serialized, transferred, or accepted after process restart.

## Goals

- Pass the unchanged maximum-live p95 <= 250 ms reference gate without changing its
  2,500 real immutable object files.
- Preserve full immutable V2 catalog generations and the current HEAD-last durable
  publication sequence.
- Preserve the current behavior for missing, zero-length, symlinked, non-regular,
  replaced-by-non-file, and unexpectedly hard-linked referenced objects.
- Preserve exact pointer, FRK/key identity, catalog-byte, object-record, recovery, and
  rotation cache binding.
- Preserve cold-path, Linux, and monitor-unavailable correctness through the existing
  capability-relative safe-open validator.
- Keep the complete public `commit` call inside the benchmark timer.

## Non-goals

- No delta, journal, Merkle, pack, or sharded catalog/object format.
- No persistent validation sidecar and no timestamp-only or directory-mtime authority.
- No weakening of catalog-byte reauthentication introduced by PR #117.
- No new assumption that an unchanged object is cryptographically rehashed on every
  commit. The current unchanged-object path does not do that.
- No privileged NTFS USN-journal dependency.
- No Linux fast path in this revision.
- No per-name invalidation on either fast-path platform; monitor names are never
  filesystem authority.
- No change to CoreFS logical operations, public APIs, object encryption, or prepared
  revision semantics.

## Existing validation contract

For each unchanged catalog object, `validate_existing_object_file` currently:

1. opens the catalog physical name relative to the pinned `objects/` capability;
2. compares metadata from the opened file with non-following metadata from the linked
   directory entry;
3. requires both observations to be the same regular, non-symlink file;
4. requires one link in production Windows; Unix additionally recognizes one exact
   crash-stale immutable staging alias; and
5. requires nonzero length.

The validator does not rehash unchanged encrypted bytes, compare the file identity to
the identity seen on the prior commit, or create an atomic snapshot against a hostile
writer after each file is closed. The lease must preserve the five listed checks and
their timing behavior at least as strongly; it must not claim a stronger existing
content-integrity guarantee.

## Why a directory watcher alone is insufficient

A directory monitor can observe deletion, creation, rename, reparse/symlink, size, and
last-write events within `objects/`, but it is not sufficient for the current
link-count contract. A local NTFS characterization created a hard link to a referenced
object in a sibling directory while monitoring `objects/`; the source directory emitted
no event. Windows retained-handle metadata and macOS capability-relative `fstatat`
metadata still expose the increased link count.

Therefore:

- the monitor owns path-change invalidation;
- platform metadata owns per-commit identity, type, nonzero-length, and link-count
  checks;
- the Windows and macOS fast paths require exactly one link; and
- the Unix slow path retains its existing exact crash-stale-alias exception.

## Architecture

### 1. Lease state

Extend the authenticated commit snapshot with optional object lease state:

```text
ObjectValidationLease
  backend: Windows | MacOS
  directory_identity
  monitor_generation
  monitor_state: Clean | DirtyAll | Unknown
  objects: stable-ID ordered LeasedObjectBinding[]

LeasedObjectBinding
  existing ValidatedObjectBinding
  validation_anchor:
    WindowsHandle(retained capability-open File, opened_file_identity)
    | MacOsStamp(device, inode, mode/type, length, link_count)
```

The lease inherits the existing snapshot's exact `PointerSet`,
`RequiredCacheKeyIds`, authenticated catalog, and object-wrap-key identity. Reuse
requires exact equality of all existing cache fields and the full catalog object tuple.
Neither validation anchor contains plaintext, an Object DEK, an FRK, or new secret
material. The macOS stamp comes from the same opened-versus-linked safe validation that
first admits the object.

`Unknown` is terminal for that lease instance. It is dropped and rebuilt only through
a complete validating path.

### 2. Monitor contract

Introduce a crate-private `ObjectDirectoryMonitor` abstraction with three outcomes:

```text
fence() -> CleanThrough(sequence)
         | DirtyThrough(sequence)
         | Unknown(reason)
```

#### Windows monitor and fence

The Windows backend uses the already pinned `objects/` directory handle and native
directory-change notifications. It must:

- arm before the initial full object scan;
- request file-name, directory-name, attributes, size, last-write, security, and
  reparse-relevant changes;
- report buffer overflow, cancellation, handle loss, parse errors, or incomplete
  rename pairing as `Unknown`;
- preserve events that arrive between fences;
- classify every notification other than the exact active fence lifecycle as
  `DirtyAll`; notification names are never used to select one catalog object;
- return only after an implementation-specific directory-entry fence proves that
  earlier path events have been delivered; and
- ignore a fence lifecycle only when its unpredictable 8.3-compatible ASCII name and
  expected create/delete action sequence match that monitor instance's active
  operation under Windows case-insensitive comparison.

The proposed Windows fence is an exclusive create/delete lifecycle for a reserved
random 8.3-compatible probe entry inside `objects/`. Using an already-8.3-compatible
name prevents long-name/short-name aliasing from making the probe indistinguishable.
Seeing its exact terminal notification establishes the queue boundary. Any alternate
name, action, collision, or unexpected event dirties or invalidates the lease rather
than being attributed to one object. Probe cleanup is mandatory on the healthy path. A
stale probe, failed cleanup, unprovable notification ordering, or benchmark temp-file
residue makes the monitor `Unknown` and disables the fast path.

The first revision deliberately has no per-name dirty fast path. Windows does not
guarantee that directory notifications use the long name rather than an 8.3 short
name, and ordinary Windows path lookup is case-insensitive. Treating every non-probe
event as `DirtyAll` prevents alternate casing or short-name reporting from hiding a
replacement. A future targeted invalidation path requires a separately reviewed
lossless name-attribution design.

#### macOS monitor creation

The macOS backend uses a process-local FSEvents stream for the canonical physical
`objects/` directory, scheduled on a dedicated serial dispatch queue. It treats the
stream as a directory-wide invalidation signal and never trusts an event path or
filename.

Creation is an exact native contract:

1. derive the watched absolute path from the already-open pinned `objects/` directory
   descriptor with `F_GETPATH`; do not accept a caller-supplied watch path;
2. safely open the complete canonical absolute directory-component chain from namespace
   `/` through `objects/` with
   `O_EVTONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC`, prove the final descriptor is
   the already pinned `objects/` identity, and reject more than
   `MAX_MACOS_MONITORED_ANCESTORS = 64` components;
3. create one nonblocking close-on-exec `kqueue` and register every retained component
   descriptor with `EVFILT_VNODE`, `EV_ADD | EV_ENABLE | EV_CLEAR`, and
   `NOTE_RENAME | NOTE_DELETE | NOTE_REVOKE`;
4. create a per-host FSEvents stream with
   `sinceWhen = kFSEventStreamEventIdSinceNow`, latency `0.050` seconds, and exactly
   `kFSEventStreamCreateFlagWatchRoot |
   kFSEventStreamCreateFlagFileEvents |
   kFSEventStreamCreateFlagNoDefer`;
5. omit `IgnoreSelf`, because CoreFS-originated changes must not disappear from the
   conservative directory-wide state; omit `MarkSelf` and `UseCFTypes`, because neither
   self-attribution nor event paths are consumed;
6. call `FSEventStreamSetDispatchQueue` before `FSEventStreamStart`;
7. if any path-chain open, identity proof, kqueue registration, stream creation, queue
   scheduling, or start step fails, mark the candidate `Unknown`, release only the
   resources whose lifecycle state was reached, and use the safe-open path; Apple
   likewise documents
   [failed start as requiring fallback scanning](https://developer.apple.com/documentation/coreservices/1448000-fseventstreamstart);
   and
8. after successful start and before scanning any object, poll the kqueue, then
   revalidate the pinned root,
   `fs/`, `catalogs/`, and `objects/` identities and prove the canonical watch path
   still names the pinned `objects/` descriptor.

`WatchRoot` is mandatory because Apple emits
[`RootChanged`](https://developer.apple.com/documentation/coreservices/kfseventstreamcreateflagwatchroot)
only for streams created with that flag. `FileEvents` ensures direct object data,
metadata, link, and entry changes request file-level delivery, while names remain
non-authoritative. `NoDefer` reduces idle notification latency; correctness still
depends on the explicit fence, not latency.

The stream starts before the initial full object scan and remains alive for the
complete lease generation. Replacement or rename of `objects/` or an ancestor between
the first layout observation and stream start is therefore caught by the mandatory
post-start kqueue/identity check; activity after start is caught by the fence.

#### macOS root-continuity monitor

FSEvents `RootChanged` has event ID zero, so an asynchronous FSEvents event-ID
acknowledgment cannot by itself distinguish “no queued event” from “zero-ID callback
not delivered yet.” The retained ancestor descriptors plus `EVFILT_VNODE` form a
separate root-continuity monitor for that exact gap.

The kqueue is armed before the object scan. A nonblocking poll occurs after successful
FSEvents start and at both sides of every FSEvents acknowledgment fence. Any
`NOTE_RENAME`, `NOTE_DELETE`, `NOTE_REVOKE`, `EV_ERROR`, `EV_EOF`, registration loss,
descriptor identity mismatch, or kqueue read failure makes the generation `Unknown`.
Events are never cleared back to clean; polling drains the kernel queue only after
publishing the terminal state.

This uses Apple's documented
[kernel-queue vnode model](https://developer.apple.com/library/archive/documentation/Darwin/Conceptual/FSEvents_ProgGuide/KernelQueues/KernelQueues.html):
one retained event-only descriptor per watched vnode, with `kevent` returning the
requested rename/delete/revoke flags or an error. Polling uses a zero timeout; no
second callback thread or unbounded wait is introduced.

Watching the complete canonical absolute component chain from `/` is mandatory.
Starting at the mounted volume root would miss rename-away/rename-back of a renameable
mount-point ancestor above that root. The implementation spike must prove on each
supported macOS/filesystem profile that a rename/delete/revoke of every watched
component, including an ancestor above a mounted Core volume and a
rename-away/mutate/rename-back sequence, leaves a pollable vnode event before the path
can be treated as clean. If that characterization fails, the macOS backend remains on
safe-open fallback.

#### macOS callback and asynchronous fence

The C callback performs no deferred work. It wraps all Rust logic in
`catch_unwind`; a panic is converted to `Unknown` and never crosses the C ABI. Before
returning, it synchronously publishes into a small callback-state mutex:

- `Unknown` for `MustScanSubDirs`, `UserDropped`, `KernelDropped`,
  `EventIdsWrapped`, `RootChanged`, `Mount`, or `Unmount`;
- `DirtyAll` for every ordinary filesystem event, including an event for an
  unreferenced name;
- the maximum nonzero event ID processed by that callback; and
- a condition-variable wakeup for fence waiters.

The callback never acquires the commit cache, session-state, Core lock, or operation
guard. A fence caller never holds the callback-state mutex while invoking a native
FSEvents function, performing filesystem I/O, or waiting on the dispatch queue.
Apple's FSEvents guidance requires a full rescan after
[`MustScanSubDirs`](https://developer.apple.com/documentation/coreservices/1455361-fseventstreameventflags/kfseventstreameventflagmustscansubdirs/)
or [dropped events](https://developer.apple.com/documentation/coreservices/kfseventstreameventflaguserdropped).

The native macOS fence uses
[`FSEventStreamFlushAsync`](https://developer.apple.com/documentation/coreservices/1441727-fseventstreamflushasync?language=occ),
not `FlushSync`:

1. reject invocation from the callback queue using a dispatch-queue-specific key;
2. check cancellation and poll the root-continuity kqueue;
3. call `FSEventStreamFlushAsync` without the callback-state mutex held;
4. treat its returned event ID as the largest nonzero event ID queued for this stream
   at the fence;
5. when the ID is nonzero, wait on the condition variable until the callback has
   synchronously published that ID or a later one;
6. when the ID is zero, make no claim that a zero-ID callback was acknowledged;
7. cancel immediately or time out after `2` seconds as `Unknown`;
8. poll the root-continuity kqueue again; and
9. revalidate the complete retained component chain, pinned root, `fs/`, `catalogs/`,
   and `objects/` identities and canonical watched-path binding before returning
   `Clean`.

A clean lease generation has never processed an ordinary event: the first event makes
that generation `DirtyAll`, and it is never reset to clean. This makes a nonzero async
flush target an acknowledgment point, not persistent event-history authority.
`RootChanged` still maps to callback `Unknown`, but cleanliness never depends on that
zero-ID callback arriving: the separately armed vnode queue records root/ancestor
rename, delete, or revoke history even when the original path and identities are
restored before inspection. Process-lifetime stream and kqueue continuity are the only
event evidence; event IDs are not serialized or replayed.

The macOS fast path never retains one file descriptor per object. After initial
safe-open validation, each clean hit calls
`fstatat(objects_dir_fd, physical_name, ..., AT_SYMLINK_NOFOLLOW)` and compares the
result with the cached device/inode stamp. It requires the linked entry to remain the
same regular file with nonzero length and exactly one link. Missing entries, symlinks,
directories, inode replacement, truncation, or unexpected hard links therefore cannot
pass the fast path. A link-count value of two does not use the fast path even when it
might be the exact Unix crash-stale staging case; the existing safe-open validator
decides that exception.

FSEvents may coalesce directory activity, which is acceptable because any callback
dirties the whole lease. The stream's file-event/name detail is diagnostic only. The
backend does not require or claim one event per mutation.

#### macOS teardown

macOS has no monitor thread to join. After session cancellation has woken fence waiters
and all admitted operation guards have drained, teardown runs from a non-callback
thread:

1. set the monitor cancellation flag and notify all fence waiters;
2. call `FSEventStreamStop`;
3. call `FSEventStreamInvalidate` while the stream is still scheduled; Apple documents
   that [invalidation unschedules the dispatch queue](https://developer.apple.com/documentation/coreservices/1446990-fseventstreaminvalidate);
4. issue an empty `dispatch_sync_f` barrier to the serial callback queue, after rejecting
   self-queue execution with the queue-specific key;
5. release the FSEvents stream, allowing its context release hook to drop the
   stream-held `Arc`, and only then drop the monitor's owner `Arc`; and
6. release the dispatch queue, then close the kqueue and every retained ancestor
   descriptor.

The callback context is retained by the stream's native context retain/release hooks.
The invalidation plus serial-queue barrier, not a fictitious worker join, proves no
callback can touch the context after release. No panic, timeout, cancellation, or
partial construction path may free the context or queue earlier.

Partial construction cleanup is state-sensitive:

- before an FSEvents stream exists, release locally owned CF values, queue, kqueue, and
  ancestor descriptors only;
- after stream creation but before scheduling, release the stream/context, queue,
  kqueue, and descriptors without `Stop` or `Invalidate`;
- after scheduling when `Start` returns false, call `Invalidate`, queue barrier, and
  release in the documented order, but never call `Stop`; and
- only a successfully started stream follows `Stop -> Invalidate -> barrier -> release`.

The implementation plan must begin with focused platform characterizations of both
native fences. If either backend cannot establish its stated ordered boundary, that
backend must stay on the current safe-open path and return for a new architecture
decision; failure of one backend does not weaken or silently substitute the other.

### 3. Lease construction

Lease construction follows this order:

1. acquire the existing Core-wide kernel lock;
2. revalidate the pinned root, `fs/`, `catalogs/`, and `objects/` identities;
3. read and authenticate the exact pointer/catalog state through the existing path;
4. arm the object-directory monitor through its platform creation contract;
5. revalidate the pinned layout identities after successful monitor start;
6. validate every referenced object through the existing safe-open rules;
7. construct the platform anchor from the validated opened-versus-linked observation:
   retain the opened handle and identity on Windows, or capture the linked
   device/inode/type/length/link-count stamp on macOS;
8. validate new/prepared objects through the current length and encrypted-hash path,
   returning the already validated platform anchor;
9. fence the monitor; and
10. publish `Clean` lease state only when no relevant unaccounted event occurred.

If a relevant event occurs during the scan, the implementation retries once from a
fresh pointer/layout observation under the same lock. A second event, any ambiguity, or
resource failure returns to a successful commit/load only through the uncached current
validator; it does not publish a lease.

### 4. Exact-hit commit flow

After the existing lock, layout, pointer, key-identity, and catalog-byte checks succeed:

1. clone the exact authenticated snapshot without holding the cache mutex over I/O;
2. verify the current `objects/` directory identity equals the lease identity;
3. fence and drain the monitor;
4. if the monitor reports `DirtyAll`, drop the candidate fast path and validate every
   referenced object through the current safe-open path;
5. otherwise, for every exact unchanged clean object, query fresh platform metadata:
   retained-handle metadata on Windows or capability-relative non-following `fstatat`
   metadata on macOS;
6. require a regular file, the captured file identity, nonzero length, and exactly one
   link;
7. route a metadata error, identity/type mismatch, zero length, or non-unit link count
   through
   `open_regular_file_in` and the platform's existing slow checks;
8. validate new and changed objects through the complete prepared-revision path;
9. perform a final monitor fence before publication; retry or fall back if a referenced
   path changed during validation;
10. continue unchanged canonical serialization, encryption, immutable publication, and
    durable HEAD-last advancement; and
11. replace lease state only after durable authority is established.

Every object-directory event other than the Windows backend's exact active probe
lifecycle invalidates the lease even when the changed name is not referenced by the
catalog. This conservative rule avoids trusting platform-dependent notification
spelling or FSEvents coalescing. A new hard link outside `objects/` still produces no
required directory event, so fresh per-object link-count metadata remains mandatory.

### 5. Invalidated-lease slow path

The slow path remains the existing authority:

- missing path: error;
- zero length: `ReferencedObjectMissing`;
- symlink/reparse or non-regular replacement: invalid layout;
- opened-versus-linked identity mismatch: invalid layout;
- link count other than one on Windows: invalid layout;
- link count other than one on Unix: existing crash-stale alias proof or invalid
  layout; and
- new/changed prepared revision: exact size plus complete encrypted-byte SHA-256 and
  prepared-token/key-binding checks.

A successful complete validation constructs an entirely new candidate platform-anchor
set. It does not mutate the currently authoritative lease in place.

### 6. Publication, failures, and recovery

Lease publication follows the existing disk-authority boundary:

- failure before HEAD leaves prior disk authority intact but retains `DirtyAll` or
  `Unknown` monitor state;
- durable HEAD success may publish the exact next lease;
- post-HEAD marker, callback, or invalidation failure that returns recovery-pending
  clears lease authority;
- receipt-only, completion-only, missing-HEAD, divergent-pointer, and ambiguous recovery
  states perform no lease hit;
- successful recovery publishes a lease only after a complete object validation and
  exact pointer reauthentication; and
- cache poisoning or monitor-thread panic clears the lease and uses the normal path.

The lease never makes a missing catalog acceptable. Every distinct catalog named by
HEAD, receipt, and completion is still reopened and SHA-256-verified before cached
decoded state is consumed.

### 7. Rotation

FRK rotation preserves object files but changes catalog/key authority. Rotation may
carry platform validation anchors into a candidate next lease only when:

- old and new pointer sets are both authenticated;
- the exact object physical tuple is unchanged;
- directory identity and monitor continuity are unchanged;
- every anchor passes fresh platform metadata validation; and
- cutover completion is durably verified.

Any mixed-key, recovery-pending, monitor-unknown, or callback-failure state drops the
lease. A cold full validation remains valid behavior.

### 8. Concurrency and lock ordering

The fixed order is:

```text
CoreCommitLock
  -> clone cache snapshot under short cache mutex
  -> release cache mutex
  -> monitor fence/drain
  -> platform metadata and safe-open I/O
  -> catalog validation/publication
  -> release kernel lock
  -> callbacks with no cache or monitor guard held
```

No cache or monitor mutex may be held during kernel-lock acquisition, catalog/object
I/O, crypto, failure hooks, invalidation callbacks, or user build callbacks.

Each coordinator owns its own monitor and platform anchors. A second coordinator's commit
changes pointers and produces directory events; pointer mismatch already prevents an
exact cache hit. External changes that race after one object's metadata check retain the
same non-atomic boundary as the current sequential safe-open loop and leave the lease dirty for
the next operation. The final monitor fence strengthens path-change detection during
the loop without claiming atomic protection against arbitrary hostile open handles.

### 9. Unlock-scoped session ownership

The benchmark already keeps one `CoreCommitCoordinator` alive across commits, but the
current Python FFI constructs a new coordinator inside each CoreFS call. Product use
would otherwise discard the lease immediately and make this optimization
benchmark-only.

Add a native `CorefsSession` PyO3 class that owns one
`Arc<CoreCommitCoordinator>` for one canonical Core root/Core ID. The server
`UnlockSession` owns that native session alongside its non-persisted CoreFS keys:

```text
UnlockSession
  user_id
  deks
  corefs_keys
  corefs_session -> native CorefsSession
```

Current CoreFS read/validation calls must accept and reuse the session coordinator. The
CoreFS API route obtains it only from the already resolved `UnlockSession`; request
payloads and route-local root/Core-ID/key tuples cannot select or substitute a native
coordinator. Future logical mutators then inherit the same coordinator instead of
creating an ephemeral one. Neither native session nor keys are written to the
dev-session snapshot; restored sessions that previously held CoreFS authority remain
invalidated as today.

Every native operation first acquires an `OperationGuard` from this state machine:

```text
Open(active = N)
  admit -> Open(active = N + 1)
  release -> Releasing(active = N, terminal_close = false)
  close -> Closing(active = N)

Releasing(active = N, terminal_close = false)
  admit -> reject
  release -> wait for Open or Closed
  close -> Closing(active = N)
  guard drop -> Releasing(active = N - 1)
  active = 0 -> lease teardown -> Open

Closing(active = N)
  admit -> reject
  release -> reject
  close -> wait for Closed
  guard drop -> Closing(active = N - 1)
  active = 0 -> teardown -> Closed

Closed
  admit -> reject
  close -> no-op
```

The state uses a mutex plus condition variable; it is separate from the commit-cache
and monitor locks. A guard is held through the complete Rust operation, including
publication and lease replacement, and decrements the active count on every return or
panic unwind. A terminal-close flag is monotonic: once any caller requests close,
`Releasing` atomically upgrades to `Closing`, release may never return the state to
`Open`, and every concurrent close caller waits until `Closed`.

`CorefsSession.close()` is idempotent and follows this order:

1. transition `Open -> Closing` and reject new operation guards;
2. set terminal close, signal monitor cancellation, and wake any admitted operation
   blocked in a monitor fence;
3. wait until every already-admitted operation guard has drained;
4. perform backend-specific monitor drain: join the Windows worker, or run the macOS
   stop/invalidate/serial-queue-barrier sequence;
5. clear the coordinator cache and drop every retained handle or macOS stamp and every
   budget permit;
6. transition to `Closed`; and
7. return only after no operation or monitor callback can publish or touch released
   state.

The blocking drain/join portion of the PyO3 close method runs inside
`Python::allow_threads` so an admitted operation that must reacquire the GIL can
finish. Monitor fence waits are bounded and cancellation-aware; cancellation returns
`Unknown` so an admitted operation can complete through safe-open fallback or return
its existing typed failure rather than deadlocking close. No backend monitor worker or
callback invokes Python.

Logout, token revocation, user-session replacement, expiry purge, store clear, and
server shutdown must close removed native sessions. The Python session-store lock must
not be held while cancellation waits, backend drain, or native resource destruction
runs:
the store first makes removed sessions unreachable under its lock, then closes their
native resources outside the lock.

While an unlock session is active, its Core is in use. This revision requires
logout/lock before external drive removal. It exposes an idempotent
`release_object_lease()` operation for later transfer/export and PCF-008 eject
coordination, but does not claim unimplemented desktop suspend/eject hooks. Release
uses the explicit `Releasing` state: temporarily reject new operations, cancel/wake the
monitor, drain admitted operations outside the GIL, and clear/drain the lease. It
returns to `Open` only if no terminal close was requested; otherwise close owns
teardown and all callers wait for `Closed`. A later admitted operation may rebuild a
fresh lease.

### 10. Resource and fallback policy

Each fast-path backend must reserve its object-entry and monitor resources before
advertising a clean lease. Allocation, monitor creation, Windows handle-open, or macOS
stamp-capture failure disables that backend's lease without failing an otherwise valid
CoreFS operation.

`MAX_OBJECT_LEASE_ENTRIES` is `4_096`. The coordinator checks the exact next catalog
object count before arming a monitor or constructing candidate anchors:

- `0..=4_096`: eligible for a lease;
- above `4_096`: safe-open fallback, recorded as ineligible for that exact catalog
  pointer/object-count state; and
- any partial candidate acquisition failure: atomically drop the monitor and every
  candidate anchor before continuing through safe-open fallback.

The crate also owns one process-wide `LeaseBudget` with:

```text
MAX_PROCESS_OBJECT_LEASE_ENTRIES = 4_096
MAX_PROCESS_OBJECT_LEASES = 4
MAX_PROCESS_OBJECT_LEASE_MONITOR_RESOURCES = 260
```

Before arming a monitor, a candidate atomically reserves its exact object-entry count,
exact platform monitor-resource count, and one lease slot. Reservation returns one
`LeaseSlotPermit` plus splittable `EntryPermit` and `MonitorResourcePermit` units. The
slot stays with the monitor lease; each Windows retained handle or macOS stamp owns one
entry unit, so sharing an exact anchor by `Arc` does not double-count it. macOS stamps
contain fixed metadata and do not retain per-object file descriptors; one macOS lease
may retain at most 64 canonical ancestor descriptors plus one kqueue descriptor, and
the separate process monitor-resource budget reserves that exact count before arming.
Windows reserves its monitor handle through the same budget. The entry budget still
bounds per-object memory and validation work. Candidate
failure, lease clear/replacement, session close, or panic drops the corresponding
permits and returns the counters. The process budget means many unlock tokens can
remain functionally valid, but only candidates fitting the remaining shared budget use
the fast path; all others use safe-open fallback.

An exact clean normal commit does not construct or reserve a second lease. After both
monitor fences and every platform-metadata check pass, the next authenticated snapshot
shares the existing `Arc<ObjectValidationLease>`, including its anchors, monitor, and
single RAII permit bundle. Rebinding the snapshot to the new exact pointer/key authority
does not mutate the lease's object set. Repeated unchanged 2,500-object commits
therefore keep process usage at exactly 2,500 entries and one lease: 2,500 retained
handles on Windows or 2,500 fixed stamps with no per-object descriptors on macOS.

If the catalog object set changes, the first revision does not mutate the published
lease in place. It invalidates and removes the cache's lease reference before complete
safe-open validation, then attempts a new candidate after released references return
their permits. Unchanged per-object anchors may be shared by `Arc` only when their
complete catalog tuple and monitor continuity remain exact; every new anchor owns its
own budget unit. If other readers retain the old snapshot or the complete candidate
cannot fit the remaining process budget, the commit remains correct through safe-open
validation but publishes no new lease. A later operation may retry once the budget
epoch changes.

Budget denial is keyed by the tuple of observed process-budget epoch, authenticated
object-set fingerprint, and requested object count. The fingerprint is derived from the
exact pointer-authenticated stable-ID/physical-object tuples, so a generation-only
pointer advance with the same object set does not cause repeated acquisition attempts.
Denial is not retried while all three fields remain equal. A permit release increments
the epoch, while a different authenticated object set or smaller object count also
permits a new attempt even if no unrelated lease was released. This prevents every
commit from repeating a known-impossible acquisition without leaving a session
permanently denied after its own catalog changes.
Reservation, publication, release, and epoch advance are atomic under the budget lock;
no file or monitor I/O occurs while it is held.

An over-ceiling catalog is not retried until its authenticated object count falls within
the ceiling. Transient monitor/anchor acquisition failures use process-local
exponential retry backoff from 1 second through 60 seconds; commits during backoff use
the existing safe-open path. Success resets the backoff. Tests use an injected clock
and resource factory.

Lease resources are also torn down on monitor failure, cache replacement/clear,
explicit `release_object_lease()`, or native-session close. Windows closes all retained
handles; macOS cancels and drains the FSEvents stream, closes its kqueue/ancestor
descriptors, and drops every stamp. Linux and any monitor-unavailable path retain the
current safe-open behavior.

## Error behavior

No new public error is required for an optimization miss. Monitor and lease failures
fall back internally.

Existing public errors remain exact for invalid disk state. Only failure to complete a
required full validation may fail the operation. Diagnostic probes may expose
crate-private counters/reasons for tests and benchmark attribution.

## Performance decision gate

Implementation begins with disposable platform-native release-mode spikes over 2,500
real immutable object files.

The Windows spike compares the current safe-open loop with:

1. retained-handle metadata validation;
2. the two monitor fences; and
3. conservative directory-wide invalidation overhead.

The macOS spike compares the current safe-open loop with:

1. two `FSEventStreamFlushAsync` acknowledgment fences;
2. 2,500 capability-relative `fstatat(..., AT_SYMLINK_NOFOLLOW)` calls and stamp
   comparisons;
3. the before/after nonblocking kqueue root-continuity polls; and
4. conservative directory-wide invalidation overhead.

Each spike uses real fixture object files and the same capability-relative metadata
rules as production. It changes no benchmark fixture, public timer, threshold, or
reference artifact.

The existing official p95 <= 250 ms gate and exact 30/200 artifact remain tied to the
approved Windows reference machine/profile. If the Windows combined lease validation
does not provide credible margin below that unchanged full-commit gate, implementation
stops before building recovery/rotation integration and returns for an object-pack or
broader storage-layout decision.

macOS has no invented numeric acceptance threshold in this ticket. Its fast path may
proceed only if the disposable native spike demonstrates a material and repeatable
reduction versus the same safe-open loop on the tested APFS machine, with correctness,
resource, and lifecycle gates green. The diagnostic records hardware, macOS, filesystem,
build, object count, warm-up/sample counts, and before/after distributions, but it is
not the PCF-002 reference artifact. If the native boundary or improvement is not
credible, macOS remains on safe-open fallback while the independently viable Windows
backend may proceed.

After complete correctness gates pass, run the existing disposable diagnostic and then
the exact unchanged 30/200 reference command. Only the exact reference artifact may
clear PCF-002.

## Required tests

### Monitor and platform contract

- every monitor arms before scanning;
- every fence observes prior create, delete, rename, symlink/reparse, truncate, and
  replacement events;
- events between validation and the final fence force retry/fallback;
- cancellation and monitor panic become `Unknown`;
- an inside-directory hard link dirties the whole lease; and
- an outside-directory hard link produces no required watcher event but is rejected by
  fresh platform link-count metadata.

Windows-specific coverage proves:

- buffer overflow, handle loss, malformed rename pairs, probe cleanup failure, and
  incomplete ordering become `Unknown`;
- alternate-case, short-name/8.3, unexpected ordinary-file, and unrecognized
  create/delete/rename notifications produce `DirtyAll`; and
- clean shutdown leaves no fence probe or other temporary file.

macOS-specific coverage runs on macOS and proves:

- a stream armed before the initial scan followed by an async acknowledgment fence cannot
  publish a clean lease when a mutation raced the scan;
- stream creation uses `SinceNow`, `WatchRoot`, `FileEvents`, `NoDefer`, a finite
  latency, queue-before-start ordering, and never `IgnoreSelf`;
- a bounded no-follow canonical absolute chain from `/` and kqueue vnode filters are
  armed before scanning; each ancestor rename/delete/revoke and `EV_ERROR`/`EV_EOF`
  becomes `Unknown`;
- null creation/start failure uses safe-open fallback, state-specific partial cleanup
  never calls `Stop` before successful start, and replacement of `objects/` or an
  ancestor between initial layout validation and start fails the post-start
  kqueue/identity check;
- ordinary callback batches, including coalesced or unreferenced-name events, produce
  `DirtyAll`;
- injected `MustScanSubDirs`, `UserDropped`, `KernelDropped`, `EventIdsWrapped`,
  `RootChanged`, `Mount`, and `Unmount` flags become `Unknown`;
- callback panic is contained within the C ABI, while creation/start failure, fence
  timeout, cancellation, and root-directory identity change become `Unknown`;
- a pre-validation flush and post-validation flush close the intended event window, and
  a mutation in either race seam forces retry/fallback;
- callbacks synchronously publish state and event-ID progress without a callback-needed
  mutex held across native flush or queue operations;
- an injected delayed zero-ID `RootChanged` callback cannot produce `Clean` when an
  ancestor is renamed away, an object is mutated/rebound while detached, and the same
  ancestor/path identities are restored before post-fence validation, because the
  independently armed vnode queue remains terminally `Unknown`;
- the same restored-path regression passes when a Core volume is mounted below a
  renameable parent above the volume root, proving the `/`-anchored chain covers the
  mount namespace prefix;
- teardown rejects callback-queue self-drain, follows
  cancel/stop/invalidate/barrier/release ordering, keeps its context alive through the
  barrier, and permits no callback publication after close; and
- no per-object descriptor growth occurs across repeated 2,500-object clean commits.

### Lease equivalence

- exact clean objects perform zero repeated opens;
- clean objects still perform one fresh platform-metadata validation per commit;
- missing, zero-length, symlinked, replaced-by-directory, and unexpectedly hard-linked
  objects fail on a warm hit;
- production Windows requires exactly one link, proven by a release-configuration
  integration test that does not inherit `cfg(test)` Unix compatibility;
- the macOS fast path requires exactly one link and detects missing names, zero length,
  symlinks, non-regular replacements, inode replacement, and an outside-directory hard
  link through capability-relative non-following metadata;
- a macOS two-link candidate falls back to safe-open, where only the exact recognized
  crash-stale immutable staging alias may pass;
- Unix fallback retains the recognized crash-stale immutable staging behavior;
- an invalidated lease validates all referenced regular objects with the current
  opened-versus-linked identity check;
- changed wrapped DEKs, object-key epochs, physical names, kinds, hashes, revisions, or
  key identities never reuse a leased binding; and
- wrong same-version key material still reaches authentication and fails closed.

### Authority, recovery, and concurrency

- all PR #117 HEAD/receipt/completion catalog-byte reauthentication tests remain green;
- all-missing pointers and first mutation cannot consume a stale lease;
- pre-HEAD failure cannot publish candidate anchors;
- post-HEAD recovery-pending outcomes clear the lease;
- rotation publishes only after verified cutover completion;
- two coordinators and injected external changes cannot bypass pointer or monitor
  invalidation;
- cache/monitor poison recovery has no lock inversion; and
- callbacks observe no cache or monitor guard held.

### Session ownership and resource bounds

- two FFI calls in one unlock session reuse one coordinator;
- different users, roots, Core IDs, or unlock tokens never share a coordinator;
- route-level tests prove the resolved unlock session, not caller-supplied root, Core
  ID, or key arguments, selects the native coordinator;
- an admitted operation racing logout drains before close continues, and no cache,
  lease, platform anchor, monitor, or process-budget permit can appear after close
  returns;
- release racing close atomically upgrades to terminal close and never reopens; two
  concurrent close callers both wait for `Closed`;
- the blocking close/release path does not hold the GIL or Python session-store lock;
- logout, revoke, replacement, expiry, clear, and shutdown reject new operations, drain
  the monitor backend, and release every native resource without holding the Python
  store lock;
- dev-session restore never restores a native CoreFS session or CoreFS keys;
- `release_object_lease()` is idempotent and the next eligible commit can rebuild;
- object counts `4_096` and `4_097` select lease and fallback respectively;
- multiple unlock tokens atomically exhaust the shared 4,096-entry, four-lease, and
  260-monitor-resource budgets; denied sessions use safe-open without retrying until
  the budget epoch changes, and permit release makes another session eligible;
- repeated unchanged 2,500-object commits carry one lease forward and keep process
  usage fixed at 2,500 entries/one monitor/one permit bundle without denial;
- macOS repeats that carry-forward test without growing its process file-descriptor
  count per object and never exceeds its reserved bounded ancestor/kqueue descriptor
  count;
- macOS ancestor counts `64` and `65` select lease and safe-open fallback respectively,
  and partial chain/kqueue registration failure releases every descriptor and
  monitor-resource permit;
- changed authenticated object-set/object-count state may retry a prior budget denial
  even when the global epoch is unchanged, while a generation-only advance over the
  same object set remains suppressed;
- partial acquisition drops every candidate anchor and monitor;
- injected failures obey bounded exponential backoff rather than retrying every commit;
  and
- Linux and monitor-unavailable paths retain current safe-open behavior.

### Performance and provenance

- counters prove zero unchanged-object opens and exactly one platform metadata query per
  clean object;
- the macOS disposable diagnostic records its closed-schema environment and
  before/after distributions without changing the Windows reference artifact;
- fixture object counts remain `500/2,500/0`;
- the complete public commit remains the measured interval;
- all catalog bytes remain full canonical generations;
- final HEAD/catalog counts and zero-temporary-file assertions remain unchanged; and
- the strict source/binary/Cargo.lock/target/argv report contract remains unchanged.

## Expected implementation surface

- `packages/anima-corefs/Cargo.toml`
- `Cargo.lock`
- `packages/anima-corefs/src/transaction.rs`
- `packages/anima-corefs/src/transaction/cache.rs`
- `packages/anima-corefs/src/transaction/object_lease.rs`
- `packages/anima-corefs/src/transaction/object_lease/windows.rs`
- `packages/anima-corefs/src/transaction/object_lease/macos.rs`
- `packages/anima-corefs/src/transaction/cache_tests.rs`
- `packages/anima-corefs/src/transaction/failure_tests.rs`
- `packages/anima-corefs/tests/transaction.rs`
- `packages/anima-corefs/tests/object_lease_macos.rs`
- `packages/anima-corefs/tests/rotation.rs`
- `packages/anima-corefs/src/benchmark.rs`
- `packages/anima-corefs/tests/catalog_benchmark.rs`
- `packages/anima-core/src/ffi.rs`
- `apps/server/src/anima_server/services/sessions.py`
- `apps/server/src/anima_server/services/corefs/logical.py`
- `apps/server/src/anima_server/api/routes/corefs.py`
- `apps/server/src/anima_server/api/routes/auth.py`
- `apps/server/src/anima_server/main.py`
- `apps/server/tests/test_sessions.py`
- `apps/server/tests/test_corefs_api.py`
- `apps/server/tests/test_corefs_logical.py`
- `apps/server/tests/test_corefs_catalog_benchmark.py`
- `.github/workflows/corefs-provenance.yml` for macOS compile and native integration
  coverage
- `docs/superpowers/plans/2026-07-20-corefs-catalog-commit-performance.md`
- `docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json` only after a valid
  exact reference run
- PCF-002 and PCF-000 ticket metadata/evidence

## Alternatives rejected

- **Watcher-only invalidation:** misses at least outside-directory hard-link creation and
  cannot preserve the current link-count contract on either backend.
- **Per-name Windows notification attribution:** long-name versus 8.3 reporting and
  case-insensitive lookup can hide replacement unless ambiguous events invalidate the
  whole lease.
- **NTFS USN journal:** can report link changes and survive process restarts, but it is
  NTFS-specific and requires privileged volume-journal access.
- **Retained handles without monitoring:** preserves handle identity, length, and link
  count but cannot prove that the catalog physical name still names that handle.
- **One retained descriptor per object on macOS:** can mirror the Windows model but
  consumes thousands of descriptors. FSEvents fences plus bounded `fstatat` stamps
  preserve the required checks without that pressure.
- **Per-name FSEvents attribution:** FSEvents may coalesce activity, and its paths are not
  filesystem authority. Directory-wide invalidation is simpler and fail-closed.
- **FSEvents async acknowledgment alone:** `RootChanged` uses event ID zero, making a
  zero flush target ambiguous with no event. The bounded ancestor-vnode queue is
  required to retain rename/delete/revoke history across path restoration.
- **FSEvents synchronous flush:** proves callback delivery but exposes no error,
  cancellation, or timeout result, so it cannot satisfy the approved bounded session
  close contract.
- **Persisted FSEvents replay:** unnecessary for a process-local optimization and creates
  event-ID wrap, volume-replacement, and replay-state authority that CoreFS does not
  need.
- **Skip unchanged-object validation:** changes fail-closed behavior.
- **Authenticated persistent inventory:** an attacker or external tool can change an
  object without updating the inventory; it is not filesystem authority.
- **Object packs/shards:** viable if this lease cannot meet the gate, but changes object
  layout, reads, recovery, transfer, GC, and rotation and therefore requires a broader
  approved design.
- **Weaken the benchmark or move work outside `commit`:** violates PCF-002 acceptance.

## Completion criteria

This architecture revision is complete only when:

1. each enabled platform backend's native fence characterization proves its required
   ordered boundary;
2. every correctness, recovery, rotation, concurrency, and fail-closed regression
   passes;
3. full Rust 1.75 tests, strict Clippy, formatting, Python benchmark-contract tests, and
   diff hygiene pass;
4. the unchanged Windows exact 30/200 reference artifact passes all gates, including
   maximum-live p95 <= 250 ms, and the macOS diagnostic records a material repeatable
   reduction before its backend is enabled;
5. independent specification and quality reviews have no unresolved substantive
   finding; and
6. PCF-002 and PCF-000 record the artifact, validation, and legal state transition.
