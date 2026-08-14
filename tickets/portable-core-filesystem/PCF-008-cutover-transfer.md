# PCF-008 - Cutover, transfer, and first-release validation

- Status: in_progress
- Priority: P0
- Scope: migration cutover, local ANIMA CORE transfer/recovery, release validation
- Parent: `PCF-000`
- Depends on: `PCF-001`, `PCF-002`, `PCF-003`, `PCF-004`, `PCF-005`, `PCF-006`, `PCF-007`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Spec: `docs/superpowers/specs/2026-08-02-corefs-resumable-preparation-design.md#111-packaged-desktop-writer-exclusion-for-plaintext-draft-cleanup`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-8-cutover-transfer-and-first-release-validation`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-14 05:24 MYT
- Started: 2026-08-13 18:41 MYT
- Completed:

## Goal

Perform the verified reversible-to-forward-only cutover, provide safe cold/live transfer, and validate the first release without deleting legacy Soul rollback tables.

## Deliverables

- Resumable converter orchestration and acceptance states.
- Verified SQLCipher checkpoint and copy-verify-flip from `users/<legacy-id>/anima.db` to `.anima/soul/soul.db`.
- Authenticated first-write cutover marker.
- Legacy PostgreSQL relocation, encrypted recovery bundle, and plaintext retirement after marker.
- ANIMA CORE local transfer API/client/UI with full export/restore plus advanced Soul-only and CoreFS-only recovery.
- Rust-backed `anima_core_v2` streaming container with authenticated `full`/`soul`/`fs` kinds, <=8-MiB I/O chunks, reachable-object verification, no 16-MiB total section ceiling, and backward V1/JSON import.
- Hard-drive/removable-media destination preflight, `.partial` publication, single-file output, and authenticated <=2-GiB multipart fallback for FAT32-like limits.
- Bounded V2 KDF/header validation, one normative archive AAD tuple, pre-archive record hashing, globally unique archive nonce ordinals, and <=32-MiB aggregate streaming memory excluding the fixed Argon2 workspace.
- Same-volume import staging, authenticated active-Core registry-pointer activation, retained-old-Core rollback, and crash tests at every multipart/import publication boundary.
- Legacy app tables disabled as authority but retained read-only for PCF-009.
- Protected final signed Windows, macOS, DEB, and RPM replacement-install
  evidence for plaintext-draft cleanup, including exact artifact digests,
  recorded before irreversible cutover or first-release publication.

## Acceptance

- Rollback works before the marker and is rejected after it.
- Cold and live prepared transfer exclude Runtime and restore all canonical content.
- Full backend and Bun desktop tests execute and pass.
- Fresh Runtime/cache/log/index raw scans find no seeded portable plaintext; sealed operational payloads are unlock-only.
- Existing legacy sources remain recoverable in encrypted/read-only form for the observation window.
- After the first-write marker, no service recreates the legacy `users/<id>/anima.db` layout and transfers contain the single canonical Soul file.
- A >16-MiB binary-object round trip streams without whole-archive base64 buffering and excludes Runtime/device/credential state.
- Default artifacts are `anima-core-<timestamp>.anima`, `anima-core-soul-<timestamp>.anima`, and `anima-core-fs-<timestamp>.anima`; the authenticated payload kind, not the filename, controls import.
- Soul-only restore enters `filesystem_missing`; CoreFS-only restore enters recovery/export-only mode; neither starts as a complete ANIMA.
- Export/import memory remains bounded for an artifact larger than RAM, and insufficient capacity, unsupported destination, disconnect, tampered/missing/mixed volumes, or interrupted import cannot alter the live Core.
- Soul/FS scoped credential replacement cannot unlock undeclared compartments or promote a partial artifact to `full`; CoreFS-to-Soul attachment returns `corefs_reattachment_not_supported` in V1.
- Pre-authentication KDF/header limits, exact AAD fields, record-hash semantics, global nonce monotonicity, controller-last multipart commit, same-volume staging, registry swap, and old-Core rollback all have deterministic failure-injection coverage.
- The protected package workflow passes against the final signed MSI,
  notarized PKG, DEB, and RPM; all replacement-only launch-target, native
  process-census, post-WebView capability, and source-first cleanup checks pass;
  exact artifact digests are recorded. Missing or failed evidence blocks
  cutover and release publication.

## Activity Log

- 2026-07-12 06:07 MYT - Ticket created.
- 2026-07-12 15:45 MYT - Added the approved animaOS/ANIMA CORE naming contract, local-only full/Soul/CoreFS streaming artifacts, removable-media preflight/multipart behavior, and independent recovery states.
- 2026-07-12 16:01 MYT - Closed independent-review gaps for scoped keys, normative archive crypto, controller-last multipart publication, atomic import activation, and deferred V1 reattachment.
- 2026-08-13 15:54 MYT - User approved moving PCF-004's cost-bearing final
  signed-package executions into this first-release ticket without waiving
  them. The protected workflow remains triggerless until PCF-008 is active and
  funded execution is separately authorized; irreversible cutover and release
  publication are forbidden until all four native results and exact artifact
  digests are recorded.
- 2026-08-13 18:41 MYT - Claimed by Codex on local branch
  `codex/pcf-008-cutover-transfer` from completed PCF-007 head `1067becf` after
  confirming PCF-001 through PCF-007 are done and no competing claim is
  visible. Local reversible implementation and validation may proceed; the
  triggerless paid package workflow, irreversible first-write marker, release
  publication, and merge remain unauthorized.
- 2026-08-13 18:58 MYT - Completed Step 1 locally. The manifest now records
  the exact reversible cutover states and a stable pending epoch, while only an
  authenticated committed `fs/HEAD` catalog marker can create forward-only
  session authority. Unlock repairs the crash seam after marked HEAD
  publication, rejects manifest-only authority, and blocks rollback after the
  marker. Logical reads and stable-role resolution follow the committed
  catalog after cutover but remain on `VALIDATION_HEAD` beforehand. The paid
  package workflow remains disabled and no irreversible first mutation or
  external action was performed.
- 2026-08-13 19:22 MYT - Added the first Step 2/Step 7 transfer milestone.
  Rust now provides the exact registered `anima_core_v2` fixed header and KDF,
  generation-bound encrypted inventory, closed full/Soul/CoreFS record
  allowlists, 8-MiB chunk streaming, global per-container nonce ordinals,
  authenticated footer/trailer, failed-import staging cleanup, and a binary
  round trip above the legacy 16-MiB ceiling. Python now preflights local
  capacity, writable atomic rename, and FAT-like file limits, then publishes
  verified single-file or controller-last multipart output with deterministic
  cancellation and every local publication seam covered. Step 2 and Step 7
  remain open for live snapshot pinning, native multipart-set cryptography,
  import activation, and API/UI integration. No paid workflow or irreversible
  cutover action was performed.
- 2026-08-13 19:34 MYT - Added authenticated same-volume import activation.
  Capacity preflight retains the existing Core and margin; a verified sibling
  staging Core is fsynced, renamed, and selected through a generation-monotonic
  HMAC-authenticated machine-local pointer while the prior Core remains a
  named rollback target. An authenticated activation journal recovers startup
  after the staging rename or pointer swap, terminal completion is replayable,
  pointer tampering and symbolic-link staging fail closed, and rollback swaps
  the two retained directories atomically without deleting either. All five
  activation crash seams plus rollback-after-pointer passed focused tests.
- 2026-08-13 19:39 MYT - Added the physical Soul-relocation portion of
  Step 3. Under the migration write barrier, the owner SQLCipher/SQLite
  database is WAL-checkpointed, page/cipher/FK/schema verified, hashed by a
  deterministic retained-table inventory, durably copied to
  `.anima/soul/soul.db`, independently reopened and reverified, and only then
  selected by an atomic manifest flip. The legacy encrypted database remains
  intact for pre-marker rollback; crash-after-copy resumes without overwrite,
  concurrent source mutation or target corruption cannot flip authority, and
  session/account routing follows the single canonical Soul path afterward.
  Step 3 remains open for converter-journal orchestration, parity acceptance,
  and the fresh outside-Core Runtime transition.
- 2026-08-13 19:45 MYT - Added the resumable converter coordinator portion of
  Step 3. One instance-scoped Runtime journal now drives preflight, write
  freeze, the combined PCF-004/005/006/007 portable-content converter,
  validation verification, and explicit accept or reject. Every checkpoint
  resumes idempotently; acceptance recovers a crash after pending-cutover
  publication, rejection recovers a crash after legacy rollback, failures
  require an explicit retry, and journal errors persist only a class name plus
  a class-only domain digest rather than private exception text. Step 3 remains
  open for full production API wiring, parity evidence across real fixtures,
  and the fresh outside-Core Runtime transition.
- 2026-08-13 19:56 MYT - Added the native V2 archive bridge and the first
  authenticated transfer-source boundary. Python can now invoke bounded Rust
  file export/import without base64 buffering; the live native session emits
  only the committed catalog, its authenticated pointer/cutover records, and
  objects reachable from that catalog under the session object lease. The
  server wrapper constructs manifest/Soul/recovery inputs itself, materializes
  only wrapped keyslot metadata in a short-lived private file, rejects any
  native source outside the active Core, and verifies extraction in disposable
  same-volume staging. Rust archive tests remain `6 passed`, the Python binding
  compile-check passes, and the new wrapper tests pass `3`. Step 2 and Step 7
  remain open for coherent Soul generation/checkpoint capture and one
  authenticated nonce sequence across a multipart volume set; no paid workflow
  or irreversible cutover action occurred.
- 2026-08-13 20:07 MYT - Added the first Step 6 product-facing transfer slice.
  Authenticated users can obtain an authoritative full/Soul/CoreFS estimate,
  probe an exact local destination for capacity and atomic publication, start a
  bounded background single-file export, poll progress/completion, and request
  safe cancellation without persisting the passphrase or exposing physical
  Core source paths. The desktop now presents **Export ANIMA CORE** as the
  primary flow, labels advanced recovery modes and degraded states, displays
  checkpoint/capacity/file-limit/publication/progress/verification state, and
  redirects the legacy Vault page. Backend transfer coverage passes `48`, API
  client and desktop contracts pass `31`, the desktop production build passes,
  and PyO3/Ruff/diff checks pass. Step 6 remains open for authenticated import
  activation/rollback in the UI; multipart remains visibly gated until Step 7
  provides one globally authenticated volume set. No paid workflow or
  irreversible cutover action occurred.
- 2026-08-13 20:26 MYT - Added the non-activating first-mutation milestone for
  Step 4. The existing logical planner now commits the approved first mutation
  through the same Core-wide transaction as the authenticated cutover marker,
  then advances later mutations only from committed `fs/HEAD`. The PyO3
  session accepts exact selected-snapshot identity, body encoding, principal,
  and manifest-derived cutover mode; Python reconciles a post-HEAD crash before
  choosing the mode and retains the original cutover receipt identity across
  later heads. A closed-schema HTTP dispatcher, canonical bounded base64 body
  decoding, optimistic errors, index invalidation, and client multi-target
  fail-closed behavior are implemented and tested, but the compile-time
  `CORE_FS_PUBLIC_MUTATION_ADAPTERS_READY` gate remains false until every
  content-family adapter and the funded signed-package evidence are complete.
  CoreFS mutation tests pass `7`, the focused server band passes `66`, strict
  CoreFS Clippy and the Python-enabled binding compile-check pass, and scoped
  anima-core Clippy passes with only the previously recorded unrelated crate
  lints allowed. No public mutation, paid workflow, or irreversible cutover
  action occurred.
- 2026-08-13 20:33 MYT - Closed a partial-transfer authority leak before
  exposing restore. Export now writes a stable transient manifest snapshot and
  filters root wrappers by the authenticated payload kind: Soul artifacts
  exclude every FRK wrapper and filesystem authority marker, while CoreFS-only
  artifacts exclude SQLCipher password/recovery wrappers and Soul KDF state.
  Both partial kinds carry explicit degraded state and exact archive scope;
  malformed or empty scoped keyslot sets fail closed, and the archive
  passphrase remains transport-only rather than becoming a Core credential.
  The focused archive tests pass `6` and the combined transfer/API band passes
  `51`, with Ruff, format, and diff hygiene green. Restore activation remains
  gated; no paid workflow, push, or irreversible action occurred.
- 2026-08-13 20:43 MYT - Added the non-activating restore product slice.
  Authenticated users can probe an exact archive plus same-volume staging
  parent, start/poll/cancel a bounded background extraction, and retain only a
  completely authenticated staged Core. Native record inventory, manifest
  identity/scope, exact keyslot snapshot, degraded recovery state, symlink and
  extra-file rejection, capacity, and a consume-time source/destination recheck
  all fail closed with full staging cleanup. The desktop now exposes **Verify
  and stage restore** for full, Soul-only, and CoreFS-only archives while
  clearly stating that the running Core is unchanged and activation/rollback
  remain restart-gated. The combined backend transfer band passes `60`, API
  client/desktop contracts pass `32`, and the desktop production build passes.
  No active-Core pointer, paid workflow, push, or irreversible action occurred.
- 2026-08-13 20:52 MYT - Wired the authenticated active-Core registry into
  startup before the Core lock, manifest mutation, or per-user database
  bootstrap. A 32-byte registry authentication key is generated and
  round-trip-verified only through the OS credential store; the machine-local
  registry directory is private on POSIX, pointer paths must match the Core ID
  in their bounded regular manifests, and key creation is re-read after the
  Core lock boundary to fail closed on a competing startup. Startup now
  recovers an authenticated activation journal, selects a completed full
  restore, and retains the old Core for rollback. Soul-only and CoreFS-only
  candidates are structurally forbidden from activation. Focused
  registry/transfer/startup coverage passes `56`, and the broader encrypted
  startup/auth band passes `17` with `4` environment-dependent skips. A test
  fixture was isolated from the real app-data root and the exact two temporary
  registry files accidentally created by the earlier diagnostic run were
  removed. No product activation endpoint, paid workflow, push, or irreversible
  cutover action occurred.
- 2026-08-13 20:56 MYT - Added restart-safe full-restore activation
  scheduling. The authenticated API can promote only a completed full staging
  operation into a machine-local HMAC-authenticated pending request; it cannot
  change the live pointer. The next startup consumes the exact request before
  Core resources open, re-verifies the full candidate, performs the existing
  journaled directory/pointer/completion transaction, retains the prior Core,
  and durably deletes the request. Request replay and post-activation/pre-delete
  crashes are idempotent; partial recovery modes cannot schedule. The desktop
  exposes **Activate on restart** only for verified full restores and states
  that the current Core remains active until shutdown. Combined native/server
  transfer/startup coverage passes `67`, API/desktop contracts pass `32`, and
  the desktop production build passes. No live pointer swap, paid workflow,
  push, or irreversible cutover action occurred.
- 2026-08-13 21:03 MYT - Added authenticated restart-only rollback to the
  retained prior Core. The API requires explicit confirmation and returns only
  generation/identifier/status metadata; it never exposes machine paths. The
  running pointer remains unchanged while an HMAC-authenticated rollback intent
  is pending. The next pre-resource startup re-verifies both pointer-selected
  Cores, consumes the existing atomic rollback/completion path, and deletes the
  request durably; replay after a crash following the pointer swap is
  idempotent. The desktop displays retained-Core identity and requires a checked
  confirmation before scheduling. Focused backend coverage passes `71`, API
  client/desktop contracts pass `33`, and the desktop production build passes.
  No live pointer swap, paid workflow, push, or irreversible cutover action
  occurred.
- 2026-08-13 21:13 MYT - Added the first Step 5 legacy Runtime retirement
  milestone. The stopped relocated PostgreSQL source is inventoried before and
  during a bounded 1-MiB streaming pass; paths and contents are encrypted under
  an OS-credential-held random key, and exact identity, chunk coordinates,
  hashes, inventory, footer, and completion are authenticated with one
  monotonic nonce sequence. The create-only bundle lives beneath machine-local
  instance recovery state outside `.anima/`, re-verifies after publication,
  resumes an authenticated durable partial, and never overwrites a conflicting
  bundle or replaces a missing credential. The separate plaintext retirement
  primitive refuses deletion unless forward-only CoreFS authority is durable,
  PostgreSQL is stopped, the bundle re-verifies, and a fresh Runtime database
  exists; tests only exercise temporary fixtures. Recovery/relocation/
  orchestration/cutover coverage passes `46`. Automatic server stop, fresh
  Runtime switch, and post-marker orchestration remain open. No paid workflow,
  push, real plaintext deletion, or irreversible cutover action occurred.
- 2026-08-13 21:21 MYT - Wired the Step 5 recovery primitive into restart
  lifecycle selection. Before default embedded PostgreSQL can open after a
  forward-only marker, the stopped retained source must produce/re-verify its
  encrypted bundle; startup then selects the fresh Runtime directory rather
  than the still-present legacy source. Only after the fresh database starts,
  claims the exact Core/instance identity, applies pgvector/Alembic, and
  initializes Runtime indexes can the legacy plaintext directory be retired.
  An explicit external Runtime follows the same post-binding/schema gate, and
  a live or ambiguous `postmaster.pid` fails closed even if a caller reports
  the source stopped. Crash-before-fresh-start and crash-before-retirement
  preserve the authenticated bundle and plaintext for retry. The expanded
  recovery/relocation/orchestration/cutover/startup/runtime band passes `96`;
  the focused final recovery/runtime rerun passes `55`. First-mutation process
  restart coordination remains open. No paid workflow, push, real source
  deletion, or irreversible cutover action occurred.
- 2026-08-13 21:34 MYT - Closed the live Soul/CoreFS snapshot-coherence
  portion of Steps 2 and 7. Soul-bearing estimate/probe/export now uses
  SQLite/SQLCipher online backup from a pinned read transaction after a
  complete WAL checkpoint, verifies page/cipher/FK/schema plus deterministic
  table hashes on the standalone snapshot, preserves SQLCipher encryption,
  and removes the private temporary database on success or failure. Full and
  CoreFS exports also freeze `fs/HEAD`, re-read the authenticated native
  inventory after Soul capture, and make the native session reject a changed
  generation/catalog hash before streaming while retaining its object lease.
  The private Soul inventory and catalog hashes are used only as in-process
  preflight fences and are not returned by the API. Transfer/Soul coverage
  passes `70`, native archive coverage passes `6`, the Python-enabled binding
  compiles, and a real SQLCipher regression proves the snapshot remains
  encrypted. Native multipart-set authentication, backward V1 import, and the
  remaining cutover gates stay open. No paid workflow, external action, or
  irreversible mutation occurred.
- 2026-08-13 21:42 MYT - Closed the explicit V1 CoreFS reattachment boundary
  in Step 6. Only the owner of a completed authenticated CoreFS-only staged
  recovery can reach the attachment operation, and it always returns HTTP 409
  `corefs_reattachment_not_supported` without exposing paths or internal
  details. Full/Soul/incomplete operations remain ordinary transfer
  precondition failures, and the API client exposes the same closed operation
  for recovery UI callers. Focused backend coverage passes `9` and API-client
  coverage passes `30`. Authenticated browse/export and scoped credential
  replacement for recovery-only mode remain open. No external or irreversible
  action occurred.
- 2026-08-13 21:58 MYT - Added authenticated read-only CoreFS recovery
  browsing for a completed FS-only staged import. Each bounded stat/list/read
  request opens the exact staged Core under a one-request password or recovery
  phrase, permits only the filtered filesystem compartment while
  authenticating each retained wrapper under its original source-scope AAD,
  rechecks the authenticated manifest, keyslot, HEAD, and catalog record hashes
  before and after use, pins the imported filesystem generation, closes the
  native session, and retains no credential or derived session. Credential
  work is precharged/rate-limited. Pre-cutover archives use only a serialized,
  byte-exact temporary `VALIDATION_HEAD` alias derived from the authenticated
  `HEAD`, and every success/failure removes it. Failures expose neither
  credential, machine staging path, nor internals, and
  import status no longer returns the staging path. The desktop exposes the
  recovery browser without offering attachment or activation. The broader
  transfer band passes `76`, API-client/desktop contracts pass `33`, and the
  desktop production build passes. Recovery-only re-export and scoped
  credential replacement remain open. No external or irreversible action
  occurred.
- 2026-08-13 22:21 MYT - Added scoped credential replacement for completed
  CoreFS-only staged recovery. One current password or recovery phrase unwraps
  only the authenticated FS compartment; fresh password and recovery
  generations wrap exactly the retained FRKs at `fs` scope, independently
  reopen before publication, and preserve `recovery_only` without Soul
  authority. Keyslot inventory publishes before manifest authority, injected
  failures restore both original control files byte-for-byte, and the updated
  authenticated control hashes gate later access. The manager retains only a
  boolean readiness flag and new hashes, the API precharges expensive attempts
  and exposes stable failures, and the desktop explicitly confirms replacement
  then shows the new phrase only in the immediate response. The broader
  transfer band passes `82`, API-client/desktop contracts pass `33`, the
  desktop production build passes, and scoped Ruff/format/diff hygiene is
  clean. Recovery-only re-export remains open. No external or irreversible
  action occurred.
- 2026-08-13 22:35 MYT - Added recovery-only CoreFS re-export from the exact
  authenticated staged import. A request-local password or recovery phrase
  opens only the FS compartment, derives an ephemeral native session, and
  never consults the active Core. Preflight and streaming use the explicit
  staged root/manifest, pin the authenticated generation and native object
  lease, recheck manifest/keyslot/catalog controls around streaming, forbid the
  active and staged Core as destinations, and close the derived session on all
  exits. Publication reuses verified cancellable single-file `.partial`
  semantics; failure cannot alter either staged or running Core. The client
  and desktop expose export/progress/cancel while clearing both credentials.
  The broader transfer band passes `86`, API-client/desktop contracts pass
  `33`, the desktop production build passes, and scoped Ruff/format/diff
  hygiene is clean. Multipart remains open. No external or irreversible action
  occurred.
- 2026-08-13 22:54 MYT - Added the first Step 8 writable-authority adapters.
  An authenticated forward-only session now routes task create/update/delete
  and portable-preference patches only through native optimistic CoreFS
  mutations, advances every local session marker to the trusted committed
  generation, and invalidates active Runtime indexes without writing the
  retained legacy task/preferences source. Task creation uses a caller-bound
  native opaque ID so its canonical body and catalog identity publish in one
  generation; deletion moves the exact revision to the new stable
  `core.trash` recovery root. Random task API IDs remain within JavaScript's
  exact-integer range and never require a legacy SQL allocator. The task and
  preference API band passes `11`, the complete diary migration band passes
  `40`, native mutation coverage passes `7`, and scoped Ruff/Rust format/diff
  hygiene passes. Other content-family writers and raw post-cutover scans keep
  Step 8 open. No external action or irreversible live cutover occurred.
- 2026-08-13 23:04 MYT - Extended Step 8 through the complete presence
  preference boundary. After authenticated cutover, presence GET/PUT and all
  background consent readers now authenticate the canonical CoreFS
  preferences object; update retains the existing consent serialization lock
  but commits only one native optimistic preference revision and never changes
  the legacy `presence_configs` row or invokes validation preparation. A
  forward-only process without an unlocked canonical session fails closed
  instead of consulting stale SQL. The focused CoreFS preference band passes
  `7`, and the account-migration plus initiative/dream consent band passes
  `152`; scoped Ruff, format, and diff hygiene pass. Account, conversation,
  diary, asset/document writers and raw post-cutover scans keep Step 8 open. No
  external action or irreversible live cutover occurred.
- 2026-08-13 23:17 MYT - Added canonical account identity and onboarding
  authority. Post-cutover user GET/PUT, `/auth/me`, login hydration, setup
  completion, and identity-override checks now authenticate the encrypted
  account-profile object; username/display/demographic updates commit one
  native optimistic revision and remain valid across logout/login while the
  retained `users` row and `agent_profile.setup_complete` stay unchanged.
  Pre-cutover user edits now refresh the validation shadow. The old
  directory-only account deletion path is rejected after cutover with a stable
  restart-required response until whole-Core deletion coordination exists.
  Account/login/onboarding coverage passes `50`, with scoped Ruff and diff
  hygiene green. Restart-safe account deletion, conversation/diary/assets/
  documents and raw scans keep Step 8 open. No external or irreversible action
  occurred.
- 2026-08-13 23:32 MYT - Added the canonical thread-lifecycle authority
  adapter. After cutover, thread create/reuse, reset/clear, close, list/read,
  and delete use authenticated bounded CoreFS snapshots and one native
  optimistic commit; retained Runtime thread/message rows remain unchanged.
  Atomic close-plus-create and thread-plus-segment trash use caller-bound
  opaque IDs, and the shared patch format now preserves exact canonical JSON
  bytes for added files without a final newline. The end-to-end regression
  proves migrated visible content remains readable while post-cutover
  lifecycle operations avoid legacy writes. Conversation/chat coverage passes
  `36`, native patch/CoreFS coverage passes `22`, and scoped lint/diff hygiene
  passes. Visible message append/edit/delete and the remaining writer families
  keep Step 8 open. No external or irreversible action occurred.
- 2026-08-13 23:49 MYT - Routed ordinary blocking and streaming agent turns
  through canonical conversation authority. User and terminal assistant bodies
  now append as optimistic authenticated message events, while fresh Runtime
  rows retain only CoreFS message/event references and null visible bodies;
  canonical history drives subsequent prompts. Segment names are thread-scoped
  to prevent cross-thread catalog collisions, and a Runtime migration adds the
  reference metadata. Route and migration coverage passes `15`. Approval
  resume, visible edit/delete, attachments, the remaining writer families, and
  raw scans keep Step 8 open. No external or irreversible action occurred.
- 2026-08-13 23:56 MYT - Closed the post-cutover approval/resume seam.
  Approval prompts and tool arguments remain unlock-sealed operational Runtime
  state with null raw fields, resumed traces allocate collision-free step and
  Runtime sequence identities, and the resumed visible assistant response
  appends only to the canonical CoreFS transcript. Canonical references now
  carry their source sequence separately from Runtime ordering. The combined
  approval/agent/persistence/chat regression band and scoped Ruff/diff gates
  pass. Visible edit/delete, attachments, remaining writer families, and raw
  scans keep Step 8 open. No external or irreversible action occurred.
- 2026-08-14 05:24 MYT - Completed canonical visible-message transitions.
  Edit and delete now append immutable authenticated events with exact prior
  event/version preconditions, share the same bounded tail retry and native
  optimistic commit path as creation, preserve immutable message identity, and
  update the canonical thread count without Runtime/Soul mutation. Stale edits
  fail closed and deletion is terminal. Conversation/migration coverage passes
  `15` with scoped Ruff/diff hygiene green. Attachments, remaining writer
  families, and raw scans keep Step 8 open. No external or irreversible action
  occurred.

## Validation

- Commands:
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_cutover.py apps/server/tests/test_corefs_indexer.py apps/server/tests/test_dev_session_continuity.py -q` (`82 passed`)
  - `cargo test -p anima-corefs` (complete native suite passed)
  - `cargo clippy -p anima-corefs --all-targets -- -D warnings` (passed)
  - `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 cargo check -p anima-core --features python` (passed)
  - scoped Ruff check/format, Rust format, and `git diff --check` (passed)
  - `cargo test -p anima-core core_archive` (`6 passed`)
  - scoped `cargo clippy -p anima-core --lib` with only unrelated pre-existing
    crate lints allowed (`passed`; the unchanged strict crate-wide invocation
    remains blocked by existing `cards.rs`, `frame.rs`, and `path_engine.rs`
    warnings)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py -q`
    (`31 passed`)
  - scoped Ruff check/format and `git diff --check` (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py -q`
    after import activation (`42 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_soul_relocation.py apps/server/tests/test_corefs_cutover.py apps/server/tests/test_auth.py -q`
    (`35 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_security_hardening.py apps/server/tests/test_runtime_db.py -q`
    (`70 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_orchestration.py apps/server/tests/test_corefs_cutover.py apps/server/tests/test_corefs_soul_relocation.py apps/server/tests/test_corefs_transfer.py -q`
    (`76 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_runtime_privacy.py -q`
    (`52 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_archive_transfer.py -q`
    (`3 passed`)
  - `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 cargo check -p anima-core --features python`
    after adding the archive bindings (passed)
  - `cargo test -p anima-core core_archive --lib` after binding the committed
    inventory (`6 passed`)
  - scoped Ruff and `git diff --check` for the archive bridge (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_archive_transfer.py apps/server/tests/test_corefs_transfer_api.py -q`
    (`48 passed`)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/corefs-transfer.test.ts`
    (`31 passed`)
  - `bun run --cwd apps/desktop build` (passed)
  - `cargo test -p anima-corefs logical::mutation --lib` (`7 passed`)
  - `cargo clippy -p anima-corefs --all-targets -- -D warnings` (passed)
  - `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 cargo check -p anima-core --features python`
    after adding logical mutation binding (passed)
  - scoped strict anima-core Clippy with only the recorded unrelated
    `cards.rs`, `frame.rs`, and `path_engine.rs` lints allowed (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_logical.py apps/server/tests/test_corefs_cutover.py apps/server/tests/test_corefs_api.py -q`
    (`66 passed`)
  - `bun test packages/api-client/tests/client.test.ts` (`28 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_archive_transfer.py apps/server/tests/test_corefs_transfer_api.py -q`
    after scoping partial-transfer key material (`51 passed`)
  - scoped Ruff check/format and `git diff --check` after scoping archive
    manifests (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_archive_transfer.py apps/server/tests/test_corefs_transfer_api.py -q`
    after non-activating restore staging/API integration (`60 passed`)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/corefs-transfer.test.ts`
    after restore staging client/UI integration (`32 passed`)
  - `bun run --cwd apps/desktop build` after restore staging UI integration
    (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_active_core_registry.py apps/server/tests/test_corefs_transfer.py apps/server/tests/test_health_startup.py apps/server/tests/test_corefs_transfer_api.py -q`
    after startup registry integration (`56 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_encrypted_core_regression.py apps/server/tests/test_auth.py -q`
    (`17 passed`, `4 skipped`)
  - scoped Ruff check/format and `git diff --check` for active-Core startup
    integration (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_active_core_registry.py apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_archive_transfer.py apps/server/tests/test_corefs_transfer_api.py -q`
    after restart-safe activation scheduling (`67 passed`)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/corefs-transfer.test.ts`
    after activation scheduling UI/client wiring (`32 passed`)
  - `bun run --cwd apps/desktop build` after activation scheduling UI wiring
    (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_active_core_registry.py apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_archive_transfer.py apps/server/tests/test_corefs_transfer_api.py -q`
    after retained-Core restart rollback integration (`71 passed`)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/corefs-transfer.test.ts`
    after rollback confirmation/status wiring (`33 passed`)
  - `bun run --cwd apps/desktop build` after retained-Core rollback UI wiring
    (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_legacy_runtime_recovery.py apps/server/tests/test_corefs_legacy_runtime.py apps/server/tests/test_corefs_orchestration.py apps/server/tests/test_corefs_cutover.py -q`
    after the encrypted legacy Runtime recovery milestone (`46 passed`)
  - scoped Ruff check/format and `git diff --check` for legacy Runtime recovery
    (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_legacy_runtime_recovery.py apps/server/tests/test_corefs_legacy_runtime.py apps/server/tests/test_corefs_orchestration.py apps/server/tests/test_corefs_cutover.py apps/server/tests/test_health_startup.py apps/server/tests/test_runtime_db.py -q`
    after restart lifecycle integration (`96 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_legacy_runtime_recovery.py apps/server/tests/test_runtime_db.py -q`
    final focused rerun (`55 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_archive_transfer.py apps/server/tests/test_corefs_transfer_api.py apps/server/tests/test_corefs_soul_relocation.py -q`
    after coherent Soul/CoreFS snapshot capture (`70 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_encrypted_core_regression.py -k 'verified_soul_snapshot_remains_sqlcipher_encrypted' -q`
    (`1 passed`, real SQLCipher snapshot remains encrypted)
  - `cargo test -p anima-core core_archive --lib` after catalog-bound native
    streaming (`6 passed`)
  - `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 cargo check -p anima-core --features python`
    after catalog-bound native streaming (passed)
  - `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 cargo check -p anima-core --features python --tests`
    (passed; compiles the stale-catalog pre-stream rejection regression)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer_api.py -q`
    after explicit V1 CoreFS reattachment rejection (`9 passed`)
  - `bun test packages/api-client/tests/client.test.ts` after the same client
    boundary (`30 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_archive_transfer.py apps/server/tests/test_corefs_transfer_api.py apps/server/tests/test_corefs_recovery_access.py -q`
    after authenticated CoreFS recovery browsing (`76 passed`)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/corefs-transfer.test.ts`
    after recovery browser client/UI wiring (`33 passed`)
  - `bun run --cwd apps/desktop build` after recovery browser UI wiring
    (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_archive_transfer.py apps/server/tests/test_corefs_transfer_api.py apps/server/tests/test_corefs_recovery_access.py -q`
    after scoped CoreFS recovery credential replacement (`82 passed`)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/corefs-transfer.test.ts`
    after credential replacement client/UI wiring (`33 passed`)
  - `bun run --cwd apps/desktop build` after credential replacement UI wiring
    (passed)
  - scoped Ruff check/format and `git diff --check` after credential replacement
    (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_archive_transfer.py apps/server/tests/test_corefs_transfer_api.py apps/server/tests/test_corefs_recovery_access.py -q`
    after recovery-only CoreFS re-export (`86 passed`)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/corefs-transfer.test.ts`
    after recovery export client/UI wiring (`33 passed`)
  - `bun run --cwd apps/desktop build` after recovery export UI wiring (passed)
  - scoped Ruff check/format and `git diff --check` after recovery re-export
    (passed)
  - `uv run pytest -q apps/server/tests/test_tasks_api.py apps/server/tests/test_corefs_preferences.py`
    after the first writable-authority adapters (`11 passed`)
  - `uv run pytest -q apps/server/tests/test_corefs_diary_migration.py`
    after adding the stable authenticated trash root (`40 passed`)
  - `cargo test -p anima-corefs logical::mutation::tests --lib` (`7 passed`)
  - scoped Ruff, Rust format, and `git diff --check` after the task/preferences
    authority milestone (passed)
  - `uv run pytest -q apps/server/tests/test_corefs_preferences.py` after the
    presence authority adapter (`7 passed`)
  - `uv run pytest -q apps/server/tests/test_corefs_account_migration.py apps/server/tests/test_inner_life_ambient_dream.py apps/server/tests/test_inner_life_initiative.py`
    (`152 passed`)
  - scoped Ruff check/format and `git diff --check` after the presence
    authority adapter (passed)
  - `uv run pytest -q apps/server/tests/test_users.py apps/server/tests/test_auth.py apps/server/tests/test_corefs_account_migration.py apps/server/tests/test_creation_flow.py`
    after canonical account/onboarding routing (`50 passed`)
  - scoped Ruff check and `git diff --check` after the account authority
    adapter (passed)
  - `uv run pytest -q apps/server/tests/test_threads_api.py apps/server/tests/test_corefs_conversation_migration.py apps/server/tests/test_chat.py`
    after the canonical thread lifecycle adapter (`36 passed`)
  - `cargo test -p anima-file-tools --test patch` (`15 passed`)
  - `cargo test -p anima-corefs logical::mutation::tests --lib` (`7 passed`)
  - scoped Ruff check and `git diff --check` after the thread lifecycle
    adapter (passed)
  - `uv run pytest -q --tb=short apps/server/tests/test_threads_api.py apps/server/tests/test_corefs_conversation_migration.py`
    after canonical blocking/streaming agent persistence (`15 passed`)
  - `uv run pytest -q --tb=short apps/server/tests/test_agent_service.py apps/server/tests/test_agent_persistence.py apps/server/tests/test_chat.py`
    after canonical agent persistence (`101 passed`)
  - `uv run pytest -q --tb=short apps/server/tests/test_threads_api.py apps/server/tests/test_approval_reentry.py apps/server/tests/test_agent_persistence.py apps/server/tests/test_agent_service.py apps/server/tests/test_chat.py`
    after canonical approval/resume persistence (passed)
  - `uv run pytest -q --tb=short apps/server/tests/test_threads_api.py apps/server/tests/test_corefs_conversation_migration.py`
    after canonical message edit/delete transitions (`15 passed`)
  - direct Python-enabled anima-core unit-test linking remains unavailable on
    this macOS extension-module host because Python symbols are not linked;
    the same binding compiles, while its transaction behavior is covered in
    anima-corefs and its Python authority/request behavior is covered by the
    server band above
- Changed paths:
  - `apps/server/src/anima_server/services/corefs/cutover.py`
  - `apps/server/src/anima_server/services/sessions.py`
  - `apps/server/tests/test_corefs_cutover.py`
  - `packages/anima-core/src/ffi.rs`
  - `packages/anima-corefs/src/logical/backend.rs`
  - `packages/anima-corefs/src/transaction/converter.rs`
  - `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md`
  - `tickets/portable-core-filesystem/PCF-000-portable-core-filesystem.md`
  - `tickets/portable-core-filesystem/PCF-008-cutover-transfer.md`
  - `packages/anima-core/src/core_archive.rs`
  - `packages/anima-core/{Cargo.toml,src/lib.rs}` and `Cargo.lock`
  - `apps/server/src/anima_server/services/corefs/transfer.py`
  - `apps/server/tests/test_corefs_transfer.py`
  - `apps/server/src/anima_server/services/corefs/soul_relocation.py`
  - `apps/server/src/anima_server/db/{session.py,user_store.py}`
  - `apps/server/tests/test_corefs_soul_relocation.py`
  - `apps/server/src/anima_server/services/corefs/orchestration.py`
  - `apps/server/tests/test_corefs_orchestration.py`
  - `apps/server/src/anima_server/services/corefs/archive_transfer.py`
  - `apps/server/tests/test_corefs_archive_transfer.py`
  - `apps/server/src/anima_server/{main.py,schemas/corefs_transfer.py}`
  - `apps/server/src/anima_server/api/routes/corefs_transfer.py`
  - `apps/server/src/anima_server/services/corefs/transfer_jobs.py`
  - `apps/server/src/anima_server/services/corefs/recovery_access.py`
  - `apps/server/tests/test_corefs_transfer_api.py`
  - `packages/api-client/src/{client.ts,types.ts}`
  - `packages/api-client/tests/client.test.ts`
  - `apps/desktop/src/{App.tsx,pages/settings/Settings.tsx,pages/settings/CoreTransferSettings.tsx}`
  - `apps/desktop/tests/corefs-transfer.test.ts`
  - `packages/anima-corefs/src/logical/{mod.rs,mutation.rs,mutation/executor.rs,mutation/tests.rs}`
  - `packages/anima-core/src/ffi.rs`
  - `apps/server/src/anima_server/{schemas/corefs.py,api/routes/corefs.py}`
  - `apps/server/src/anima_server/services/corefs/{logical.py,cutover.py}`
  - `apps/server/tests/{test_corefs_api.py,test_corefs_logical.py,test_corefs_cutover.py}`
  - `apps/server/tests/test_corefs_recovery_access.py`
  - `packages/api-client/src/types.ts`
  - `apps/server/src/anima_server/services/corefs/active_core_registry.py`
  - `apps/server/tests/test_corefs_active_core_registry.py`
  - `apps/server/tests/test_encrypted_core_regression.py`
  - `apps/server/src/anima_server/services/corefs/legacy_runtime_recovery.py`
  - `apps/server/tests/test_corefs_legacy_runtime_recovery.py`
  - `apps/server/tests/test_runtime_db.py`
  - `apps/server/src/anima_server/services/corefs/{content_authority.py,task_authority.py,task_mutations.py,preferences.py,writing_source.py}`
  - `apps/server/src/anima_server/api/routes/{tasks.py,preferences.py,presence.py}`
  - `apps/server/src/anima_server/services/presence_config.py`
  - `apps/server/tests/{test_tasks_api.py,test_corefs_preferences.py}`
  - `apps/server/src/anima_server/services/corefs/account_profile.py`
  - `apps/server/src/anima_server/api/routes/{users.py,consciousness.py}`
  - `apps/server/tests/test_users.py`
  - `apps/server/src/anima_server/services/corefs/{conversation_authority.py,conversation_mutations.py}`
  - `apps/server/src/anima_server/api/routes/{chat.py,threads.py}`
  - `apps/server/tests/{test_corefs_conversation_migration.py,test_threads_api.py}`
  - `apps/server/src/anima_server/services/agent/{persistence.py,service.py}`
  - `apps/server/src/anima_server/models/runtime.py`
  - `apps/server/alembic_runtime/versions/035_corefs_message_references.py`
  - `packages/anima-file-tools/src/patch/parser.rs`
  - `packages/anima-file-tools/tests/patch.rs`
  - `packages/anima-corefs/src/logical/{mutation.rs,mutation/patch.rs,mutation/tests.rs}`
  - `packages/anima-core/src/ffi.rs`
- Notes:
  - PCF-001 through PCF-007 are done. The four-platform signed-package gate
    remains mandatory and cost-deferred; it cannot be dispatched or waived by
    local ticket execution.
  - The macOS host can compile-check the Python-enabled PyO3 binding, but its
    extension-module test target is not locally linkable against the venv
    interpreter. The binding regression remains in the Rust test target for a
    supported native runner and Step 4 will add the end-to-end Python first-
    mutation exercise.
