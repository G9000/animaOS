# CoreFS Object Validation Lease Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pass the unchanged PCF-002 maximum-live p95 <= 250 ms Windows reference gate by replacing repeated safe opens of unchanged objects with a security-equivalent, unlock-scoped object validation lease, while adding an independently gated native macOS fast path and retaining the existing safe-open validator everywhere else.

**Architecture:** Keep disk pointers, authenticated catalog bytes, and the current safe-open validator authoritative. A clean process-local lease is bound to the exact pointer/key/catalog/object tuple, owns one conservative directory monitor plus bounded validation anchors, performs fresh platform metadata checks between two ordered monitor fences, and falls back to a complete safe-open scan on every event or uncertainty. Windows retains validated object handles; macOS retains only safe-open-derived stamps and watches the complete `/`-to-`objects/` namespace chain with FSEvents plus kqueue. One native `CorefsSession` carries the coordinator for the lifetime of an unlock session and drains every operation and monitor before release or close.

**Tech Stack:** Rust 1.75, `cap-std`, `windows-sys`, `libc`, CoreServices FSEvents, CoreFoundation, Grand Central Dispatch, kqueue, PyO3 0.22, Python 3.12/FastAPI/pytest, GitHub Actions on Windows/macOS/Linux, PowerShell, Git.

**Independent plan review:** Approved on 2026-07-23 with no remaining issues after
the final-evidence tasks were bound to a permanent production-backend diagnostic
binary, closed output schema, and exact Windows/macOS commands.

---

## Source of truth and stop rules

- Approved design: `docs/superpowers/specs/2026-07-23-corefs-object-validation-lease-design.md`
- Historical performance baseline: `docs/superpowers/plans/2026-07-20-corefs-catalog-commit-performance.md`
- Active child: `tickets/portable-core-filesystem/PCF-002-corefs-catalog.md`
- Parent tracker: `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`
- PRD acceptance: `docs/prds/portable-core-filesystem-v1.md`
- Current Windows reference: maximum-live commit p50/p95/p99 `251.3036/361.1692/442.4276` ms at 2,500 real object files; every other catalog gate passes.

Do not change the V2 catalog or object format, fixture populations, 30/200 sample counts, public commit timer, p95 thresholds, HEAD-last durability, recovery markers, catalog-byte reauthentication, prepared-revision hashing, or error contract. Do not trust watcher names, directory mtimes, timestamps, or serialized lease state.

The first two tasks are mandatory native characterizations:

- If the Windows probe cannot prove its ordered boundary or show credible release-mode margin below the unchanged 250 ms full-commit gate, stop the entire implementation and return for an object-pack or broader storage-layout decision.
- If the macOS probe cannot prove the complete namespace boundary or show material repeatable APFS improvement, keep only macOS on the existing safe-open fallback. A failed macOS gate does not block an independently viable Windows backend.
- No platform backend is enabled merely because it compiles. Its native race, resource, lifecycle, and performance gates must pass on that platform.

## File responsibility map

| Path | Responsibility in this plan |
|---|---|
| `packages/anima-corefs/src/transaction/object_lease.rs` | Platform-neutral lease, monitor outcomes, anchors, resource permits, denial/backoff, and injected test seams |
| `packages/anima-corefs/src/transaction/object_lease/windows.rs` | Retained-handle metadata, conservative directory monitor, unpredictable 8.3 probe fence, cancellation, and worker drain |
| `packages/anima-corefs/src/transaction/object_lease/macos.rs` | FSEvents callback/fence, `/`-anchored vnode chain, kqueue polling, `fstatat` stamps, partial cleanup, and queue-barrier teardown |
| `packages/anima-corefs/src/transaction/object_lease_tests.rs` | Platform-neutral state, budget, carry-forward, denial, retry, poison, and injected monitor tests |
| `packages/anima-corefs/src/transaction/cache.rs` | Exact authenticated snapshot binding to optional shared lease state |
| `packages/anima-corefs/src/transaction/cache_tests.rs` | Exact-hit authority, lock ordering, counters, and cache/lease publication tests |
| `packages/anima-corefs/src/transaction.rs` | Lease construction, two-fence validation, safe-open fallback, publication, recovery, rotation, and explicit release |
| `packages/anima-corefs/src/transaction/failure_tests.rs` | Pre/post-HEAD, recovery-pending, callback, panic, and concurrent-coordinator failure coverage |
| `packages/anima-corefs/tests/transaction.rs` | Public Windows/macOS filesystem-equivalence and release-configuration tests |
| `packages/anima-corefs/tests/object_lease_macos.rs` | Native macOS FSEvents/kqueue race, restored-path, descriptor, and teardown coverage |
| `packages/anima-corefs/tests/rotation.rs` | FRK cutover and lease carry/drop behavior |
| `packages/anima-corefs/src/benchmark.rs` | Closed-schema lease counters and diagnostic-only platform observations |
| `packages/anima-corefs/src/bin/object_lease_diagnostic.rs` | Permanent production-backend Windows/macOS diagnostic command used by the final native evidence gate |
| `packages/anima-corefs/tests/catalog_benchmark.rs` | Fixture, timer, object-count, full-generation, and diagnostic-counter regressions |
| `packages/anima-corefs/Cargo.toml`, `Cargo.lock` | Rust 1.75-compatible target-native dependencies/features |
| `packages/anima-core/src/ffi.rs` | PyO3 `CorefsSession`, operation guards, close/release state machine, and session-backed logical calls |
| `apps/server/src/anima_server/services/sessions.py` | Unlock-session ownership and two-phase detach/close outside the store lock |
| `apps/server/src/anima_server/services/corefs/logical.py` | Native-session construction and session-backed logical wrappers |
| `apps/server/src/anima_server/api/routes/corefs.py` | Route authority derived only from the resolved unlock session |
| `apps/server/src/anima_server/api/routes/auth.py` | Create/replacement/logout lifecycle wiring |
| `apps/server/src/anima_server/main.py` | Shutdown drain of all native CoreFS sessions |
| `apps/server/tests/test_dev_session_continuity.py` | Restore exclusion and revoke/replacement/expiry/clear close coverage |
| `apps/server/tests/test_corefs_api.py` | Route authority and session reuse coverage |
| `apps/server/tests/test_corefs_logical.py` | Native-session wrapper behavior |
| `.github/workflows/corefs-provenance.yml` | Windows/macOS native jobs plus existing Linux safe-open coverage |
| `docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json` | Final exact Windows 30/200 evidence only |
| `tickets/portable-core-filesystem/PCF-002-corefs-catalog.md` | Child progress, characterization, validation, changed paths, and final gate |
| `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md` | Parent status, dependency eligibility, and material evidence |

Keep native unsafe code inside the two platform modules. Keep callbacks free of cache, session, Core-lock, GIL, filesystem-I/O, and user-callback acquisition. Never hold the lease budget, cache, monitor-state, or Python session-store lock across native I/O, waits, teardown, or callbacks.

### Task 1: Prove the Windows fence and performance decision gate

**Files:**
- Temporarily add, then delete before commit: `packages/anima-corefs/src/bin/object_lease_windows_spike.rs`
- Modify: `packages/anima-corefs/Cargo.toml`
- Modify: `Cargo.lock` only if the spike needs an additional `windows-sys` feature
- Modify: `tickets/portable-core-filesystem/PCF-002-corefs-catalog.md`
- Modify: `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`

- [x] **Step 1: Write a failing native characterization harness**

Add a Windows-only release binary that creates 2,500 real immutable object files and has explicit modes for safe-open validation, retained-handle metadata, monitor arm, pre/post probe fences, create/delete/rename/reparse/truncate/replace races, outside-directory hard links, buffer overflow, cancellation, malformed rename pairing, and probe cleanup failure.

The binary must emit a closed JSON record containing OS/build/filesystem/storage identity, object count, warm-up/sample counts, safe-open and lease p50/p95/p99, notification outcomes, probe residue count, handle deltas, and an `orderedBoundaryProven` boolean. It must fail if any non-probe event returns `Clean`, an outside hard link survives fresh link-count metadata, or a probe file remains.

- [x] **Step 2: Run the harness before the native implementation**

Run:

```powershell
cargo +1.75.0 run --release --locked -p anima-corefs --bin object_lease_windows_spike -- --objects 2500 --warmups 30 --samples 200 --output $env:TEMP\corefs-object-lease-windows.json
```

Expected: FAIL because the Windows monitor/fence backend is not implemented.

- [x] **Step 3: Implement only the disposable Windows probe**

Use the pinned `objects/` handle, one cancellation-aware notification worker, retained safe-open handles, fresh `GetFileInformationByHandle`-equivalent identity/type/length/link-count observations, and an exclusive unpredictable 8.3-compatible ASCII create/delete probe. Classify all non-probe events as `DirtyAll`; classify overflow, cancellation, handle loss, parse error, alternate probe spelling/action, incomplete rename pairing, collision, cleanup failure, and unprovable ordering as `Unknown`.

- [x] **Step 4: Prove the ordered boundary under mutation**

Run the spike's race modes repeatedly:

```powershell
cargo +1.75.0 run --release --locked -p anima-corefs --bin object_lease_windows_spike -- --objects 2500 --race-samples 200 --mutation-matrix --output $env:TEMP\corefs-object-lease-windows-races.json
```

Expected: PASS; every mutation before either completed fence is `DirtyAll` or `Unknown`, outside-directory hard links are rejected by fresh handle link count, cancellation terminates within two seconds, and residue/handle leaks are zero.

- [x] **Step 5: Apply the Windows stop gate**

Inspect both JSON records. Continue only if the boundary is proven and the combined two-fence plus 2,500-metadata-query p95 has credible margin below the unchanged 250 ms full-commit limit. Do not substitute a smaller fixture or subtract work from the measured loop.

- [x] **Step 6: Remove the disposable binary and restore dependency scope**

Delete `object_lease_windows_spike.rs`. Remove spike-only dependencies/features, run `cargo metadata --locked --no-deps --format-version 1`, and verify `git status --short` contains only ticket evidence.

- [x] **Step 7: Record the decision and commit**

Add the exact commands, environment, distributions, boundary result, and go/stop decision to PCF-002 and the parent. If stopped, set the legal blocker/state and end the plan.

```powershell
git add tickets/portable-core-filesystem/PCF-002-corefs-catalog.md tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md
git commit -m "docs: record Windows CoreFS lease characterization"
```

### Task 2: Prove the macOS namespace fence and APFS decision gate

**Files:**
- Temporarily add, then delete before commit: `packages/anima-corefs/src/bin/object_lease_macos_spike.rs`
- Modify: `packages/anima-corefs/Cargo.toml`
- Modify: `Cargo.lock`
- Modify: `tickets/portable-core-filesystem/PCF-002-corefs-catalog.md`
- Modify: `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`

- [ ] **Step 1: Write a failing macOS-only characterization harness**

Add a release binary that uses the pinned `objects/` descriptor to derive its absolute path, opens the complete no-follow `O_EVTONLY` directory chain from `/`, registers each descriptor with one nonblocking close-on-exec kqueue, starts an FSEvents stream with exact `SinceNow | WatchRoot | FileEvents | NoDefer` settings on a serial queue, and exposes injected creation/start/callback/fence/teardown states.

The harness must compare the existing safe-open loop with two async acknowledgment fences plus 2,500 `fstatat(..., AT_SYMLINK_NOFOLLOW)` stamp checks and two-sided kqueue polling. Its closed JSON record must include hardware, macOS, filesystem, build, object count, warm-up/sample counts, distributions, maximum descriptor delta, lifecycle results, restored-path results, and `orderedBoundaryProven`.

- [ ] **Step 2: Run the harness before implementation on native macOS**

Run on an APFS macOS host:

```bash
cargo +1.75.0 run --release --locked -p anima-corefs --bin object_lease_macos_spike -- --objects 2500 --warmups 30 --samples 200 --output /tmp/corefs-object-lease-macos.json
```

Expected: FAIL because the native macOS fence is not implemented.

- [ ] **Step 3: Implement only the disposable macOS probe**

Use thin, audited FFI kept inside the probe. Queue before `Start`; poll and revalidate after start; wrap the callback in `catch_unwind`; synchronously publish terminal state, maximum nonzero callback event ID, and a condition-variable wakeup. Use `FlushAsync`, a two-second cancellation-aware wait, kqueue polls before and after, and complete path/identity revalidation. Implement state-specific partial cleanup and successful `cancel -> Stop -> Invalidate -> dispatch_sync_f barrier -> stream/context release -> queue release -> kqueue/descriptor close`.

- [ ] **Step 4: Prove the zero-ID and mount-namespace race**

Create a disposable APFS image mounted below a renameable temporary parent. For every watched component, including an ancestor above the mounted volume root, exercise rename/delete/revoke and rename-away/mutate-or-rebind/rename-back while delaying a zero-ID `RootChanged` callback.

Run:

```bash
cargo +1.75.0 run --release --locked -p anima-corefs --bin object_lease_macos_spike -- --objects 2500 --race-samples 200 --mount-restored-path --output /tmp/corefs-object-lease-macos-races.json
```

Expected: PASS; the vnode queue stays terminally `Unknown`, ordinary events become `DirtyAll`, all documented dropped/root/mount flags become `Unknown`, callback panic stays inside the C ABI, and teardown has no callback-after-release or descriptor leak.

- [ ] **Step 5: Apply the macOS backend gate**

Enable future macOS implementation only if the boundary is proven and the native lease loop shows a material, repeatable reduction versus the same safe-open loop. Do not invent a numeric PCF-002 macOS threshold. If either condition fails, record `macOS=safe-open fallback` and skip macOS production Tasks 5-specific implementation while continuing Windows.

- [ ] **Step 6: Remove the disposable binary and commit the evidence**

Remove the probe and spike-only dependencies. Record commands, environment, distributions, lifecycle/resource evidence, and the platform decision in both tickets.

```bash
git add tickets/portable-core-filesystem/PCF-002-corefs-catalog.md tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md
git commit -m "docs: record macOS CoreFS lease characterization"
```

### Task 3: Build the platform-neutral lease and process budget

**Files:**
- Create: `packages/anima-corefs/src/transaction/object_lease.rs`
- Create: `packages/anima-corefs/src/transaction/object_lease_tests.rs`
- Modify: `packages/anima-corefs/src/transaction.rs:1-65,986-1045`
- Modify: `packages/anima-corefs/src/transaction/cache.rs:182-333`

- [ ] **Step 1: Add failing state and budget tests**

Add tests named:

```rust
lease_state_is_terminal_after_dirty_or_unknown()
exact_budget_reservation_is_atomic_and_raii_released()
budget_enforces_4096_entries_four_leases_and_260_monitor_resources()
entry_and_monitor_permits_are_not_double_counted_when_arc_is_shared()
budget_denial_retries_only_after_epoch_or_object_set_change()
generation_only_pointer_change_keeps_same_denial_suppressed()
transient_failure_backoff_runs_from_one_to_sixty_seconds()
catalog_counts_4096_and_4097_select_eligible_and_fallback()
partial_candidate_failure_releases_every_permit()
```

Use an injected monotonic clock and resource factory. Run:

```powershell
cargo +1.75.0 test --locked -p anima-corefs object_lease -- --nocapture
```

Expected: FAIL because the module and types do not exist.

- [ ] **Step 2: Define the platform-neutral contract**

Implement crate-private `MonitorState::{Clean, DirtyAll, Unknown}`, `FenceOutcome`, platform-tagged `ValidationAnchor`, ordered `LeasedObjectBinding`, and `ObjectValidationLease`. `Unknown` and `DirtyAll` must be terminal for a generation. Unsupported platforms must return an optimization miss and preserve safe-open behavior.

- [ ] **Step 3: Implement exact process-wide RAII accounting**

Use one `OnceLock<LeaseBudget>` with:

```rust
MAX_OBJECT_LEASE_ENTRIES = 4_096;
MAX_PROCESS_OBJECT_LEASE_ENTRIES = 4_096;
MAX_PROCESS_OBJECT_LEASES = 4;
MAX_PROCESS_OBJECT_LEASE_MONITOR_RESOURCES = 260;
MAX_MACOS_MONITORED_ANCESTORS = 64;
```

Reserve the exact entry count, monitor-resource count, and lease slot atomically before monitor I/O. Make slot, entry, and monitor-resource permits RAII-owned; increment the budget epoch on release; never perform file/native I/O under the budget lock.

- [ ] **Step 4: Implement eligibility, denial, and backoff**

Key budget denial by `(budget_epoch, authenticated_object_set_fingerprint, requested_count)`. Suppress an identical denial, permit retry after an object-set/count change or permit-release epoch, treat over-ceiling state separately, and inject the 1/2/4/.../60-second transient backoff clock.

- [ ] **Step 5: Keep lease destruction outside the cache mutex**

Extend cache state only enough to hold an optional `Arc<ObjectValidationLease>`. Ensure `CommitCache::replace`, `clear`, and poison recovery move discarded snapshots out of the mutex before dropping them. Add a destructor probe test that fails if monitor/permit destruction occurs while `cache.inner` is held.

- [ ] **Step 6: Run focused and regression tests**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::object_lease_tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs transaction::cache_tests -- --nocapture
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/object_lease.rs packages/anima-corefs/src/transaction/object_lease_tests.rs
git commit -m "corefs: add bounded object lease state"
```

### Task 4: Implement the Windows retained-handle monitor backend

**Files:**
- Create: `packages/anima-corefs/src/transaction/object_lease/windows.rs`
- Modify: `packages/anima-corefs/src/transaction/object_lease.rs`
- Modify: `packages/anima-corefs/Cargo.toml`
- Modify: `Cargo.lock`
- Test: `packages/anima-corefs/src/transaction/object_lease_tests.rs`
- Test: `packages/anima-corefs/tests/transaction.rs:250-415`

- [ ] **Step 1: Add failing Windows notification and anchor tests**

On Windows, add tests for arm-before-scan, both fence seams, exact active probe lifecycle, alternate case, short/8.3 names, unreferenced names, create/delete/rename/reparse/truncate/replace, overflow, malformed rename pairs, handle loss, cancellation, cleanup failure, outside-directory hard links, and zero residue after shutdown.

Add a non-`cfg(test)` release-configuration integration case proving production Windows rejects a two-link object and does not inherit the Unix crash-stale-alias exception.

Run:

```powershell
cargo +1.75.0 test --locked -p anima-corefs windows_object_lease -- --nocapture
```

Expected: FAIL because the backend is absent.

- [ ] **Step 2: Implement retained validation anchors**

Convert the already safe-open-validated object file into an owned retained handle plus captured stable identity. On every clean hit, query fresh metadata from that handle and require the same regular-file identity, nonzero length, and exactly one link. Do not retain plaintext, DEKs, FRKs, or additional ambient paths.

- [ ] **Step 3: Implement the conservative notification worker**

Arm the pinned directory before scanning. Request file-name, directory-name, attributes, size, last-write, security, and reparse-relevant notifications. Preserve events across fences. Only the exact current unpredictable 8.3-compatible ASCII probe create/delete sequence is fence traffic; every other event is `DirtyAll`, and every ambiguity/failure is `Unknown`.

- [ ] **Step 4: Implement cancellation and teardown**

Make the worker cancellation-aware, unblock native reads on release/close, join it outside cache/session locks, remove a healthy probe, and keep `Unknown` terminal on cancellation, cleanup failure, worker panic, or handle loss.

- [ ] **Step 5: Run native and fallback coverage**

```powershell
cargo +1.75.0 test --locked -p anima-corefs windows_object_lease -- --nocapture
cargo +1.75.0 test --release --locked -p anima-corefs cache_hit_rejects_unexpected_hard_link -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs cache_hit_rejects -- --nocapture
```

Expected: PASS with no probe residue.

- [ ] **Step 6: Commit**

```powershell
git add packages/anima-corefs/Cargo.toml Cargo.lock packages/anima-corefs/src/transaction/object_lease.rs packages/anima-corefs/src/transaction/object_lease/windows.rs packages/anima-corefs/src/transaction/object_lease_tests.rs packages/anima-corefs/tests/transaction.rs
git commit -m "corefs: add Windows object lease monitor"
```

### Task 5: Implement the independently gated macOS FSEvents and kqueue backend

Skip this task and retain the safe-open fallback if Task 2 did not clear the macOS gate.

**Files:**
- Create: `packages/anima-corefs/src/transaction/object_lease/macos.rs`
- Create: `packages/anima-corefs/tests/object_lease_macos.rs`
- Modify: `packages/anima-corefs/src/transaction/object_lease.rs`
- Modify: `packages/anima-corefs/Cargo.toml`
- Modify: `Cargo.lock`

- [ ] **Step 1: Add failing native construction and partial-cleanup tests**

Add macOS-only tests for exact stream flags, queue-before-start, `F_GETPATH`-derived watch path, complete no-follow `/` chain, final identity proof, kqueue registration, ancestor limits 64/65, post-start poll/revalidation, null create, failed start, and failure at every partial construction state.

Run on macOS:

```bash
cargo +1.75.0 test --locked -p anima-corefs --test object_lease_macos construction -- --nocapture
```

Expected: FAIL because the backend is absent.

- [ ] **Step 2: Implement audited native ownership wrappers**

Keep all `unsafe` FFI in `macos.rs`. Add only Rust 1.75-compatible direct target dependencies required by the proven spike. Wrap CF values, stream/context, dispatch queue, kqueue, and descriptors in state-aware RAII types. Prohibit `Stop` before successful `Start`; after scheduling plus failed start, use `Invalidate -> barrier -> release`.

- [ ] **Step 3: Add failing callback and fence tests**

Inject ordinary batches, coalesced/unreferenced events, all required dropped/root/mount flags, delayed zero-ID `RootChanged`, callback panic, timeout, cancellation, kqueue `EV_ERROR`/`EV_EOF`, descriptor identity mismatch, and mutations in both fence seams.

Expected: callback panic never crosses the ABI; ordinary events are `DirtyAll`; every ambiguous flag/failure is terminal `Unknown`.

- [ ] **Step 4: Implement synchronous callback publication and async fences**

Use `catch_unwind`, a small mutex/condition variable, maximum nonzero processed event ID, and no deferred callback work. Reject fence/teardown on the callback queue with a queue-specific key. Call `FlushAsync` with no callback mutex held, wait at most two seconds for nonzero target acknowledgment, poll kqueue before and after, and revalidate the complete chain plus pinned root/`fs`/`catalogs`/`objects` identities and watched-path binding.

- [ ] **Step 5: Implement stamp validation**

Capture device/inode/type/length/link count only after existing opened-versus-linked safe validation. On clean hits, use capability-relative `fstatat(..., AT_SYMLINK_NOFOLLOW)`; require the same regular inode, nonzero length, and exactly one link. Route two links and every mismatch/error to the existing safe-open validator so only its exact crash-stale alias may pass.

- [ ] **Step 6: Implement terminal teardown**

After cancellation and operation draining, enforce `Stop -> Invalidate -> dispatch_sync_f barrier -> stream/context release -> owner Arc drop -> queue release -> kqueue/descriptor close`. Prove no callback publication occurs after close and no per-object descriptor growth occurs across repeated 2,500-object validations.

- [ ] **Step 7: Run the complete macOS native matrix**

```bash
cargo +1.75.0 test --locked -p anima-corefs --test object_lease_macos -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs macos_object_lease -- --nocapture
```

Expected: PASS, including mounted-volume restored-path coverage and fixed descriptor bounds.

- [ ] **Step 8: Commit**

```bash
git add packages/anima-corefs/Cargo.toml Cargo.lock packages/anima-corefs/src/transaction/object_lease.rs packages/anima-corefs/src/transaction/object_lease/macos.rs packages/anima-corefs/tests/object_lease_macos.rs
git commit -m "corefs: add macOS object lease monitor"
```

### Task 6: Construct leases only through a complete validating scan

**Files:**
- Modify: `packages/anima-corefs/src/transaction.rs:986-1090,2843-3080,3786-3970`
- Modify: `packages/anima-corefs/src/transaction/cache.rs:182-333`
- Modify: `packages/anima-corefs/src/transaction/object_lease.rs`
- Test: `packages/anima-corefs/src/transaction/cache_tests.rs`
- Test: `packages/anima-corefs/tests/transaction.rs`

- [ ] **Step 1: Add failing construction-order tests**

Add tests proving:

```rust
candidate_monitor_arms_before_initial_object_scan()
candidate_revalidates_layout_after_monitor_start()
candidate_fence_covers_the_complete_safe_open_scan()
event_during_scan_retries_once_then_falls_back_without_lease()
partial_anchor_failure_drops_monitor_anchors_and_permits()
pre_head_failure_never_publishes_candidate_lease()
unsupported_platform_commits_through_safe_open_without_lease()
```

Run:

```powershell
cargo +1.75.0 test --locked -p anima-corefs lease_candidate -- --nocapture
```

Expected: FAIL because commit validation does not construct candidates.

- [ ] **Step 2: Return validated anchors from the existing validator**

Refactor `validate_existing_object_file` and `validate_prepared_file` so the same safe-open observation can produce a platform anchor after all existing opened-versus-linked, type, symlink/reparse, link-count, nonzero-length, prepared-size, encrypted-hash, token, and key-binding checks pass. Do not open an object a second time solely to construct its anchor.

- [ ] **Step 3: Add candidate construction around the full scan**

After lock/layout/pointer/catalog authentication and next-catalog construction, check exact object count and denial/backoff state, reserve budget, arm the monitor, revalidate layout, scan every object, and fence. Publish `Clean` only when all checks and the fence are clean. Retry a scan-race once from a fresh pointer/layout observation; a second event or any uncertainty completes only through safe-open validation with no lease.

- [ ] **Step 4: Bind the candidate to exact authenticated authority**

Store the ordered full catalog object tuple, directory identity, monitor generation, validated bindings, and permit bundle in `ObjectValidationLease`. Attach it to `AuthenticatedCommitSnapshot` only after durable HEAD authority and final pointer/key derivation are reauthenticated. Recovery-pending and pre-HEAD failures must hold no candidate authority.

- [ ] **Step 5: Run construction and legacy-equivalence tests**

```powershell
cargo +1.75.0 test --locked -p anima-corefs lease_candidate -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs cache_hit_rejects -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests -- --nocapture
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/object_lease.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/tests/transaction.rs
git commit -m "corefs: construct leases from full validation"
```

### Task 7: Use exact clean leases and carry them forward without double reservation

**Files:**
- Modify: `packages/anima-corefs/src/transaction.rs:2843-3210,3786-3970`
- Modify: `packages/anima-corefs/src/transaction/cache.rs:282-378`
- Modify: `packages/anima-corefs/src/transaction/object_lease.rs`
- Test: `packages/anima-corefs/src/transaction/cache_tests.rs`
- Test: `packages/anima-corefs/src/transaction/object_lease_tests.rs`
- Test: `packages/anima-corefs/tests/transaction.rs`

- [ ] **Step 1: Add failing exact-hit and carry-forward tests**

Add tests proving zero repeated opens, exactly one fresh platform metadata query per clean object, pre/post fences, full fallback on any event, no trust in event names, exact pointer/key/catalog/object binding, and rejection after changes to wrapped DEK, key epoch, physical name, kind, hash, revision, key identity, or same-version key material.

Add repeated unchanged 2,500-object commits that assert `Arc::ptr_eq` on the lease and fixed usage of 2,500 entries, one lease, one permit bundle, and the exact platform monitor-resource count.

- [ ] **Step 2: Verify the RED phase**

```powershell
cargo +1.75.0 test --locked -p anima-corefs clean_lease -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs lease_carry_forward -- --nocapture
```

Expected: FAIL because unchanged objects still call `open_regular_file_in`.

- [ ] **Step 3: Implement the exact clean-hit path**

Clone the exact snapshot under the short cache mutex, release it, prove directory identity, run the first fence, query every exact anchor's fresh metadata, route each mismatch/error/non-unit link through `open_regular_file_in`, validate changed/prepared objects fully, run the final fence, and only then continue unchanged serialization/encryption/publication.

- [ ] **Step 4: Implement terminal invalidation and full fallback**

`DirtyAll` and `Unknown` must never return to `Clean`. Any monitor event, fence ambiguity, anchor mismatch, changed object set, or final-fence event must validate all referenced regular objects through the existing opened-versus-linked safe path. No per-name fast path is allowed.

- [ ] **Step 5: Carry forward one shared lease**

When the complete object tuple is unchanged and both fences/metadata checks pass, bind the next snapshot to the same `Arc<ObjectValidationLease>` and permit bundle. For a changed set, remove the cache's old lease reference before full validation and attempt a new reservation only after released references return permits; a budget miss publishes a correct snapshot without a lease.

- [ ] **Step 6: Run the equivalence and accounting matrix**

```powershell
cargo +1.75.0 test --locked -p anima-corefs clean_lease -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs lease_carry_forward -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs cache_hit_rejects -- --nocapture
cargo +1.75.0 test --release --locked -p anima-corefs production_link_count -- --nocapture
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/cache.rs packages/anima-corefs/src/transaction/object_lease.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/src/transaction/object_lease_tests.rs packages/anima-corefs/tests/transaction.rs
git commit -m "corefs: validate unchanged objects through leases"
```

### Task 8: Preserve failure, recovery, rotation, and lock-order authority

**Files:**
- Modify: `packages/anima-corefs/src/transaction.rs:1362-1990,2174-2428,3078-3210`
- Modify: `packages/anima-corefs/src/transaction/failure_tests.rs`
- Modify: `packages/anima-corefs/src/transaction/cache_tests.rs`
- Modify: `packages/anima-corefs/tests/rotation.rs`
- Modify: `packages/anima-corefs/tests/transaction.rs`

- [ ] **Step 1: Add failing failure-boundary tests**

Cover all-missing pointers, first mutation, pre-HEAD failure, post-HEAD recovery-pending, receipt-only, completion-only, missing HEAD, divergent pointers, missing/changed retained catalog bytes, monitor panic/poison, callbacks, and final pointer replacement. Assert no stale lease hit and no candidate publication before durable authority.

- [ ] **Step 2: Add failing rotation and concurrency tests**

Prove rotation carries anchors only after authenticated old/new tuples, unchanged object bindings, fresh metadata, monitor continuity, and verified cutover completion. Mixed-key, callback failure, monitor uncertainty, recovery-pending, and wrong same-version material must drop the lease.

Add two-coordinator tests with external path mutations and pointer changes. Assert the fixed order:

```text
CoreCommitLock -> short cache clone -> monitor fence -> metadata/safe-open I/O
-> publication -> kernel unlock -> callbacks
```

- [ ] **Step 3: Verify the RED phase**

```powershell
cargo +1.75.0 test --locked -p anima-corefs lease_failure -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs lease_rotation -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs lease_lock_order -- --nocapture
```

Expected: at least one failure in each new group.

- [ ] **Step 4: Integrate fail-closed cache clearing and rotation publication**

Clear/drop lease authority on every ambiguous recovery path, monitor poison/panic, post-HEAD recovery-pending result, and callback/publication reconciliation that cannot authenticate exact durable state. Preserve all PR #117 HEAD/receipt/completion catalog-byte reauthentication.

- [ ] **Step 5: Keep every callback outside internal guards**

Add `try_lock`/probe assertions showing no cache, monitor-state, budget, or lease guard is held during kernel-lock acquisition, filesystem I/O, crypto, failure hooks, invalidation callbacks, or build callbacks. Ensure dropped leases are destructed after guards are released.

- [ ] **Step 6: Run focused and full transaction/rotation suites**

```powershell
cargo +1.75.0 test --locked -p anima-corefs transaction::failure_tests -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs --test rotation -- --nocapture
cargo +1.75.0 test --locked -p anima-corefs --test transaction -- --nocapture
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add packages/anima-corefs/src/transaction.rs packages/anima-corefs/src/transaction/failure_tests.rs packages/anima-corefs/src/transaction/cache_tests.rs packages/anima-corefs/tests/rotation.rs packages/anima-corefs/tests/transaction.rs
git commit -m "corefs: bind leases to recovery and rotation"
```

### Task 9: Add an unlock-scoped native CorefsSession

**Files:**
- Modify: `packages/anima-core/src/ffi.rs:220-350,1069-1405,2900-2945,3032-3265`
- Modify: `packages/anima-corefs/src/transaction.rs:1015-1090`
- Test: `packages/anima-core/src/ffi.rs`

- [ ] **Step 1: Add failing native-session state-machine tests**

Add tests named:

```rust
two_calls_in_one_native_session_reuse_one_coordinator()
different_roots_or_core_ids_never_share_a_coordinator()
operation_guard_drains_before_close_releases_lease()
release_rejects_new_operations_then_returns_to_open()
close_racing_release_is_terminal_and_never_reopens()
two_close_callers_wait_for_closed()
guard_drop_on_panic_decrements_active_count()
close_cancels_a_blocked_fence_and_returns_boundedly()
no_resource_or_cache_publication_occurs_after_close_returns()
```

Run:

```powershell
cargo +1.75.0 test --locked -p anima-core --features python corefs_session -- --nocapture
```

Expected: FAIL because `CorefsSession` does not exist.

- [ ] **Step 2: Add explicit coordinator release**

Expose an idempotent coordinator `release_object_lease()` that cancels/drains the backend and clears the lease without closing the coordinator. It must not hold the cache mutex while waiting or destroying resources.

- [ ] **Step 3: Implement the native session state machine**

Add PyO3 class `CorefsSession` owning one canonical root/Core-ID `Arc<CoreCommitCoordinator>`. Implement mutex/condition-variable states `Open`, `Releasing`, `Closing`, and `Closed`, a monotonic terminal-close flag, and RAII `OperationGuard` held through the complete Rust operation including publication/lease replacement.

- [ ] **Step 4: Move logical operations onto the session coordinator**

Add session-backed methods for validation snapshot, stat, list, walk, glob, grep, read, and search readiness. Preserve existing exported free functions as compatibility/cold-path wrappers, but make server code use the session methods. The session identity, not per-call root/Core-ID arguments, selects the coordinator.

- [ ] **Step 5: Release the GIL during blocking drain**

Implement PyO3 `release_object_lease()` and `close()` so cancellation/wait/join/queue-barrier/resource destruction executes inside `Python::allow_threads`. Do not invoke Python from a native monitor worker/callback.

- [ ] **Step 6: Run native-session and existing FFI tests**

```powershell
cargo +1.75.0 test --locked -p anima-core --features python corefs_session -- --nocapture
cargo +1.75.0 test --locked -p anima-core --features python corefs_logical -- --nocapture
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add packages/anima-core/src/ffi.rs packages/anima-corefs/src/transaction.rs
git commit -m "core: add unlock-scoped CoreFS sessions"
```

### Task 10: Make the server unlock session the sole native authority

**Files:**
- Modify: `apps/server/src/anima_server/services/sessions.py:24-235,260-335`
- Modify: `apps/server/src/anima_server/services/corefs/logical.py:1-245`
- Modify: `apps/server/src/anima_server/api/routes/corefs.py:45-90`
- Modify: `apps/server/src/anima_server/api/routes/auth.py:150-370`
- Modify: `apps/server/src/anima_server/main.py:101-260`
- Modify: `apps/server/tests/test_dev_session_continuity.py`
- Modify: `apps/server/tests/test_corefs_api.py`
- Modify: `apps/server/tests/test_corefs_logical.py`

- [ ] **Step 1: Add failing ownership and route-authority tests**

Test that login/register/recovery creates one native session, two route calls reuse it, different unlock tokens/users/roots/Core IDs do not share it, and caller-supplied headers/payload/root/Core-ID/key values cannot select or substitute a coordinator. A resolved session missing either keys or native session must return the existing locked response.

- [ ] **Step 2: Add failing teardown tests**

Use a fake native session with blocking/recording `close()` to prove logout, revoke, user replacement, expiry purge, clear, and shutdown first make sessions unreachable under the store lock, then close outside that lock. Prove an admitted operation can reacquire the GIL/store without deadlock, close is called exactly once, and errors do not resurrect a token.

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --locked --project apps/server pytest apps/server/tests/test_dev_session_continuity.py apps/server/tests/test_corefs_api.py apps/server/tests/test_corefs_logical.py -q
```

Expected: FAIL because `UnlockSession` has no native session and removal zeroes data under the store lock.

- [ ] **Step 3: Add native session ownership without persistence**

Add `corefs_session` beside `corefs_keys` on `UnlockSession`. Create it from server-owned canonical `get_core_dir()` and `get_core_id()` when CoreFS keys are established. Never serialize the native session or keys. Preserve the current rule that any dev snapshot marked `hadCorefsKeys` is discarded instead of restored.

- [ ] **Step 4: Refactor the store to two-phase detach and destroy**

For create replacement, revoke, revoke-user, expiry purge, clear, and shutdown: under `_lock`, persist/apply the new reachable session map and collect removed sessions; after releasing `_lock`, call native `close()` and zero DEKs. Do not run native cancellation, wait, join, queue barrier, resource destruction, or Python secret cleanup under `_lock`.

- [ ] **Step 5: Route all logical calls through the resolved native session**

Change `CoreFsRequestContext` to carry the resolved native session plus keys; remove route-local coordinator selection by root/Core-ID. Update `logical.py` wrappers to call session-backed native methods. Keep request/response schemas unchanged.

- [ ] **Step 6: Drain sessions during application shutdown**

In the lifespan `finally` path, make the global store unreachable/empty and close removed sessions with `asyncio.to_thread` before process resources disappear. Continue shutdown if an individual close reports an error, but log it and ensure no session remains reachable.

- [ ] **Step 7: Run server lifecycle and auth regressions**

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --locked --project apps/server pytest apps/server/tests/test_dev_session_continuity.py apps/server/tests/test_corefs_api.py apps/server/tests/test_corefs_logical.py -q
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --locked --project apps/server pytest apps/server/tests/test_corefs_keyslots.py -q
uv run --locked --project apps/server ruff check apps/server/src/anima_server/services/sessions.py apps/server/src/anima_server/services/corefs/logical.py apps/server/src/anima_server/api/routes/corefs.py apps/server/src/anima_server/api/routes/auth.py apps/server/src/anima_server/main.py apps/server/tests/test_dev_session_continuity.py apps/server/tests/test_corefs_api.py apps/server/tests/test_corefs_logical.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add apps/server/src/anima_server/services/sessions.py apps/server/src/anima_server/services/corefs/logical.py apps/server/src/anima_server/api/routes/corefs.py apps/server/src/anima_server/api/routes/auth.py apps/server/src/anima_server/main.py apps/server/tests/test_dev_session_continuity.py apps/server/tests/test_corefs_api.py apps/server/tests/test_corefs_logical.py
git commit -m "server: own CoreFS leases in unlock sessions"
```

### Task 11: Lock the platform, resource, provenance, and benchmark contracts

**Files:**
- Modify: `packages/anima-corefs/src/benchmark.rs`
- Create: `packages/anima-corefs/src/bin/object_lease_diagnostic.rs`
- Modify: `packages/anima-corefs/tests/catalog_benchmark.rs`
- Modify: `apps/server/tests/test_corefs_catalog_benchmark.py`
- Modify: `.github/workflows/corefs-provenance.yml`
- Modify: `tickets/portable-core-filesystem/PCF-002-corefs-catalog.md`
- Modify: `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`

- [ ] **Step 1: Add failing diagnostic-counter tests**

Extend crate-private/test diagnostic observations to prove:

- zero unchanged-object safe opens on a clean lease;
- exactly one fresh platform metadata query per clean object;
- exactly two ordered fences per clean commit;
- fixed entry/lease/monitor-resource counts across repeated 2,500-object commits;
- full safe-open counts after `DirtyAll` or `Unknown`; and
- unchanged fixture counts, full generation serialization, timer boundary, HEAD/catalog counts, and temporary-file assertions.

Do not add lease diagnostics to the official artifact unless the existing strict schema is deliberately versioned; prefer test-only counters and a separate disposable platform record.

Also add a failing CLI contract test for a permanent
`object_lease_diagnostic` binary. Its closed JSON schema must contain:

```text
schemaVersion
platform
hardware
os
filesystem
build
objectCount
warmups
samples
safeOpen.{p50Ms,p95Ms,p99Ms}
lease.{p50Ms,p95Ms,p99Ms}
lease.{safeOpenCount,metadataQueryCount,fenceCount}
resources.{entryPermits,leasePermits,monitorResources,descriptorDelta}
correctness.{orderedBoundaryProven,mutationMatrixPassed,teardownPassed}
residueCount
```

Unknown or extra fields must fail the CLI contract test. The diagnostic is
supporting evidence only and must not write the official catalog-reference
artifact.

- [ ] **Step 2: Verify the RED phase**

```powershell
cargo +1.75.0 test --locked -p anima-corefs --test catalog_benchmark lease -- --nocapture
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --locked --project apps/server pytest apps/server/tests/test_corefs_catalog_benchmark.py -q
```

Expected: the new lease-counter case fails before probe wiring; all pre-existing contract tests remain green.

- [ ] **Step 3: Wire bounded diagnostic observations and the permanent CLI**

Add only crate-private/test or diagnostic-binary observations. Preserve the source/binary/Cargo.lock/target/argv closed-schema report contract and the complete public `commit` measured interval.

Implement `packages/anima-corefs/src/bin/object_lease_diagnostic.rs` as a
cross-platform command over the production backend:

```text
--target <create-only directory>
--objects 2500
--warmups 30
--samples 200
--mutation-matrix
--output <create-only JSON path>
```

On Windows it must run the retained-handle/probe-fence backend. On macOS it
must run the FSEvents/kqueue/`fstatat` backend. On unsupported or disabled
platforms it exits nonzero with a typed `backendUnavailable` diagnostic rather
than pretending to have measured a lease. Reuse the production monitor,
anchor, budget, and teardown code; do not copy the disposable spike
implementation into the binary.

- [ ] **Step 4: Expand native CI without weakening Linux**

Update workflow path filters for `packages/anima-core`, the touched server CoreFS/session files, and tests. Keep the standalone Ubuntu Rust 1.75 safe-open job. Add:

- Windows Rust 1.75 CoreFS native monitor, release link-count, full CoreFS, and strict Clippy checks.
- macOS Rust 1.75 native FSEvents/kqueue integration, restored-path/APFS characterization, descriptor-bound, full CoreFS, and strict Clippy checks when the macOS backend is enabled.

Use the exact native tests from Tasks 4 and 5. Do not mark them `continue-on-error`.

- [ ] **Step 5: Run local quality gates**

```powershell
cargo +1.75.0 test --locked -p anima-corefs
cargo +1.75.0 test --locked -p anima-core --features python
cargo +1.75.0 clippy --locked -p anima-corefs -p anima-core --all-targets --all-features -- -D warnings
cargo +1.75.0 fmt -p anima-corefs -p anima-core -- --check
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --locked --project apps/server pytest apps/server/tests/test_dev_session_continuity.py apps/server/tests/test_corefs_api.py apps/server/tests/test_corefs_logical.py apps/server/tests/test_corefs_catalog_benchmark.py -q
uv run --locked --project apps/server ruff check apps/server/src/anima_server/services/sessions.py apps/server/src/anima_server/services/corefs/logical.py apps/server/src/anima_server/api/routes/corefs.py apps/server/src/anima_server/api/routes/auth.py apps/server/src/anima_server/main.py apps/server/tests/test_dev_session_continuity.py apps/server/tests/test_corefs_api.py apps/server/tests/test_corefs_logical.py apps/server/tests/test_corefs_catalog_benchmark.py
bun run check:repo
git diff --check
```

Expected: all scoped code/test/format/lint gates pass. If `check:repo` reports unrelated pre-existing drift, record the exact findings without altering unrelated initiatives.

- [ ] **Step 6: Record cross-platform evidence and commit**

Record Windows/macOS/Linux results, enabled/fallback backend decisions, resource counts, and any unrelated repository-check residual in both tickets.

```powershell
git add packages/anima-corefs/src/benchmark.rs packages/anima-corefs/src/bin/object_lease_diagnostic.rs packages/anima-corefs/tests/catalog_benchmark.rs apps/server/tests/test_corefs_catalog_benchmark.py .github/workflows/corefs-provenance.yml tickets/portable-core-filesystem/PCF-002-corefs-catalog.md tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md
git commit -m "test: verify CoreFS lease platform contracts"
```

### Task 12: Run final native diagnostics and the unchanged exact reference

**Files:**
- Modify only after valid evidence: `docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json`
- Modify: `docs/superpowers/specs/2026-07-23-corefs-object-validation-lease-design.md`
- Modify: `docs/superpowers/plans/2026-07-23-corefs-object-validation-lease.md`
- Modify: `tickets/portable-core-filesystem/PCF-002-corefs-catalog.md`
- Modify: `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`

- [ ] **Step 1: Require a clean source commit before performance evidence**

Run:

```powershell
git status --short
git rev-parse HEAD
git diff --check
```

Expected: clean working tree and a committed implementation SHA. Do not generate the official artifact from uncommitted source.

- [ ] **Step 2: Run the permanent Windows production-backend diagnostic**

Use the committed production-backend diagnostic over 2,500 real files:

```powershell
cargo +1.75.0 run --release --locked -p anima-corefs --bin object_lease_diagnostic -- --target "$env:LOCALAPPDATA\animaOS\benchmarks\corefs-object-lease-diagnostic-windows" --objects 2500 --warmups 30 --samples 200 --mutation-matrix --output "$env:TEMP\corefs-object-lease-diagnostic-windows.json"
```

Expected: exit `0`; the closed record reports two fences per clean sample,
2,500 metadata queries, zero repeated safe opens, proven mutation ordering,
fixed resource counts, zero descriptor/handle growth after teardown, and zero
residue. This is supporting evidence, not the official artifact.

- [ ] **Step 3: Run the permanent macOS production-backend diagnostic when enabled**

On the approved APFS host, run:

```bash
cargo +1.75.0 run --release --locked -p anima-corefs --bin object_lease_diagnostic -- --target /tmp/anima-corefs-object-lease-diagnostic-macos --objects 2500 --warmups 30 --samples 200 --mutation-matrix --output /tmp/corefs-object-lease-diagnostic-macos.json
```

Expected: exit `0`; the closed record contains exact environment,
safe-open/lease distributions, fence/callback/kqueue results, fixed
ancestor/kqueue resources, no per-object descriptor growth, and zero residue.
If correctness/resource/lifecycle or material improvement regresses, disable
the macOS backend and retain safe-open fallback before proceeding.

- [ ] **Step 4: Run the exact unchanged Windows 30/200 reference**

Use the same create-only target handling and exact command preserved by the historical plan:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --locked --project apps/server python apps/server/scripts/benchmark_corefs_catalog.py --reference --target C:\Users\leoca\AppData\Local\animaOS\benchmarks\corefs-catalog-reference-v1
```

Expected: exit `0`, `allPassed=true`, medium p95 <= 100 ms, maximum-live p95 <= 250 ms, serialized-limit p95 <= 250 ms, durable-write p95 <= 5 ms, maximum-live size <= 16 MiB, object counts `500/2500/0`, HEAD/catalog counts intact, and zero temporary files.

- [ ] **Step 5: Independently validate provenance and the artifact**

Run:

```powershell
$env:ANIMA_CORE_REQUIRE_ENCRYPTION='false'; uv run --locked --project apps/server pytest apps/server/tests/test_corefs_catalog_benchmark.py -q
git diff --check
```

Re-run the historical read-only source/build/binary/committed-Cargo.lock/target/argv/closed-schema/count/generation/tree validator. Expected: exact implementation SHA, binary identity/hash, committed Cargo.lock hash, canonical object roots, and every gate match the artifact.

- [ ] **Step 6: Apply the final ticket state**

If the official gate is red, record exact evidence, set PCF-002 to `blocked`, keep PCF-003 dependency-ineligible, synchronize the parent, and stop for a new architecture decision.

If every gate is green, record acceptance evidence and changed paths, set PCF-002 to `done` with `Completed: <timestamp>`, move it into the parent's completed history, and make PCF-003 eligible without claiming it. Mark this plan complete and the approved spec implemented. Do not push, open a PR, request review, merge, deploy, or start PCF-003 without separate authority.

- [ ] **Step 7: Commit final evidence**

```powershell
git add docs/benchmarks/portable-core-filesystem/catalog-reference-v1.json docs/superpowers/specs/2026-07-23-corefs-object-validation-lease-design.md docs/superpowers/plans/2026-07-23-corefs-object-validation-lease.md tickets/portable-core-filesystem/PCF-002-corefs-catalog.md tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md
git commit -m "docs: record CoreFS lease reference evidence"
```
