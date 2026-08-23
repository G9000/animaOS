# PCF-008 - Cutover, transfer, and first-release validation

- Status: done
- Priority: P0
- Scope: greenfield authority, local ANIMA CORE transfer/recovery, release validation
- Parent: `PCF-000`
- Depends on: `PCF-001`, `PCF-002`, `PCF-003`, `PCF-004`, `PCF-005`, `PCF-006`, `PCF-007`
- Owner: Codex
- PRD: `docs/prds/portable-core-filesystem-v1.md`
- Spec: `docs/superpowers/specs/2026-07-12-portable-core-filesystem-design.md#approved-greenfield-release-amendment-2026-08-16`
- Plan: `docs/superpowers/plans/2026-07-12-portable-core-filesystem.md#task-8-cutover-transfer-and-first-release-validation`
- Created: 2026-07-12 06:07 MYT
- Updated: 2026-08-23 07:45 MYT
- Started: 2026-08-13 18:41 MYT
- Completed: 2026-08-23 07:45 MYT

## Goal

Ship greenfield Portable Core authority, provide safe cold/live current-format transfer, and validate the first release without any unreleased compatibility surface.

## Deliverables

- Direct greenfield Soul/CoreFS/Runtime bootstrap with canonical authority from the first private write.
- Removal of pre-release SQLCipher/PostgreSQL/browser/transcript migration and fallback paths.
- ANIMA CORE local transfer API/client/UI with full export/restore plus advanced Soul-only and CoreFS-only recovery.
- Rust-backed `anima_core_v2` streaming container with authenticated `full`/`soul`/`fs` kinds, <=8-MiB I/O chunks, reachable-object verification, no 16-MiB total section ceiling, and strict rejection of unsupported earlier formats.
- Hard-drive/removable-media destination preflight, `.partial` publication, single-file output, and authenticated <=2-GiB multipart fallback for FAT32-like limits.
- Bounded V2 KDF/header validation, one normative archive AAD tuple, pre-archive record hashing, globally unique archive nonce ordinals, and <=32-MiB aggregate streaming memory excluding the fixed Argon2 workspace.
- Same-volume import staging, authenticated active-Core registry-pointer activation, retained-old-Core rollback, and crash tests at every multipart/import publication boundary.
- No packaged legacy-writer exclusion or plaintext-draft cleanup authority.

## Acceptance

- Greenfield bootstrap publishes only canonical current-format authority.
- Cold and live prepared transfer exclude Runtime and restore all canonical content.
- Full backend and Bun desktop tests execute and pass.
- Fresh Runtime/cache/log/index raw scans find no seeded portable plaintext; sealed operational payloads are unlock-only.
- No service recognizes or recreates pre-release `users/<id>/anima.db`, embedded Runtime, plaintext browser draft, transcript archive, vault JSON, or capsule layouts.
- A >16-MiB binary-object round trip streams without whole-archive base64 buffering and excludes Runtime/device/credential state.
- Default artifacts are `anima-core-<timestamp>.anima`, `anima-core-soul-<timestamp>.anima`, and `anima-core-fs-<timestamp>.anima`; the authenticated payload kind, not the filename, controls import.
- Soul-only restore enters `filesystem_missing`; CoreFS-only restore enters recovery/export-only mode; neither starts as a complete ANIMA.
- Export/import memory remains bounded for an artifact larger than RAM, and insufficient capacity, unsupported destination, disconnect, tampered/missing/mixed volumes, or interrupted import cannot alter the live Core.
- Soul/FS scoped credential replacement cannot unlock undeclared compartments or promote a partial artifact to `full`; CoreFS-to-Soul attachment returns `corefs_reattachment_not_supported` in V1.
- Pre-authentication KDF/header limits, exact AAD fields, record-hash semantics, global nonce monotonicity, controller-last multipart commit, same-volume staging, registry swap, and old-Core rollback all have deterministic failure-injection coverage.
- Unsupported pre-release data fails closed without becoming an authority or
  requiring a paid cross-version package matrix.

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
- 2026-08-14 14:10 MYT - Routed diary entries, folders, and legacy-draft
  handoff through authenticated CoreFS authority after cutover. Migrated and
  newly created entries list from canonical bodies; create/update/unfile/
  rename/delete use native optimistic mutations and recoverable trash; draft
  revisions enforce the exact monotonic handoff token. A full route regression
  proves retained SQL folder/entry counts remain unchanged. Diary/migration
  coverage passes `51` and scoped Ruff/diff hygiene passes. Binary attachment
  mutation remains fail-closed until the asset adapter; other asset/document/
  knowledge writers and raw scans keep Step 8 open. No external or
  irreversible action occurred.
- 2026-08-14 14:29 MYT - Added the first post-cutover binary mutation adapter
  and routed diary attachment upload, authenticated download, metadata linking,
  and cover selection through `core.gallery` plus the canonical diary object.
  Uploads are bounded before allocation, byte/hash/type verified after native
  publication, and best-effort orphan cleanup uses recoverable CoreFS trash;
  retained `diary_attachments` remains unchanged. The combined diary/asset/
  document-migration band passes `59`, with scoped Ruff and diff hygiene green.
  Image/avatar/chat/document/knowledge writers and raw scans keep Step 8 open;
  no external or irreversible action occurred.
- 2026-08-14 14:38 MYT - Routed agent-avatar upload, authenticated streaming,
  profile/biography projection, replacement, and deletion through the canonical
  `core.gallery` object after cutover. The retained profile row and legacy
  plaintext avatar remain untouched and are never used as fallback authority;
  upload reads are bounded before allocation and delete uses recoverable
  CoreFS trash. The focused authority regression passes and the wider account,
  biography, creation, and asset band passes `39`, with scoped Ruff and diff
  hygiene green. Chat/image/document/knowledge writers and raw scans keep Step
  8 open; no external or irreversible action occurred.
- 2026-08-14 14:50 MYT - Routed post-cutover chat-image upload, model input,
  canonical-history projection, message-bound authenticated download, unlink,
  and recoverable deletion through CoreFS. New images create no Runtime asset,
  link, or plaintext host file; provider adapters read bounded authenticated
  bytes from the active session, and unlink appends an optimistic canonical
  message revision before trashing its unique object. The scoped legacy and
  canonical chat/image band passes `47`, with Ruff and diff hygiene green.
  Image-gallery/document/knowledge writers and raw scans keep Step 8 open; no
  external or irreversible action occurred.
- 2026-08-14 14:57 MYT - Routed post-cutover PDF upload, deduplication,
  registration, parse/reparse input, and derived-source synchronization through
  authenticated CoreFS document objects. Upload is bounded before allocation,
  creates no plaintext host file, rejects manual legacy storage paths, and
  retains only sealed/rebuildable Runtime document, chunk, and source
  projections bound to the canonical URI. The broader upload/workflow/store/
  reparse/migration/asset band passes `104`, with the focused authority rerun,
  Ruff, and diff hygiene green. Image-gallery/knowledge writers and raw scans
  keep Step 8 open; no external or irreversible action occurred.
- 2026-08-14 15:16 MYT - Routed post-cutover pasted text, Markdown, web
  capture, HTML upload, deterministic derived compile/search, and offline HTML
  re-extraction through authenticated canonical knowledge-source documents.
  Each source retains both its exact original content and normalized snapshot;
  re-extraction advances the same stable source revision without network
  access, and no Runtime source/artifact/span/concept row is created. Migrated
  gallery images now resolve directly from the authenticated catalog, persist
  retention policy in an encrypted CoreFS sidecar, and use source-first
  recoverable trash without mutating retained Runtime image rows. The widened
  knowledge/image/HTML/structured/web band passes `99`, with scoped Ruff
  hygiene green. OKF bundle import/export/lint and the full raw scans keep Step
  8 open; no external or irreversible action occurred.
- 2026-08-14 15:25 MYT - Completed the remaining post-cutover OKF boundary.
  Bounded zip import validates traversal, entry count, expanded size, special
  files, encryption, frontmatter, and duplicate slugs before publishing stable
  canonical source revisions; retry is idempotent and creates no Runtime
  concepts or links. Export and lint rebuild only from authenticated CoreFS
  projections. The knowledge/OKF/autocompile band passes `61`, with scoped
  Ruff green. The full fresh-Runtime/cache/log/index raw scan remains before
  Step 8 can close; no external or irreversible action occurred.
- 2026-08-14 15:32 MYT - Added and passed the bounded raw privacy release
  gate against a real stopped embedded PostgreSQL data directory plus exact
  instance cache, health-log, and index roots. Eight portable/message/chunk/
  OCR/source/candidate/pending/preview/vector markers exist only inside
  authenticated ciphertext; streaming validation returns opaque labels rather
  than private bytes or absolute paths, rejects symlinks and concurrent file
  changes, detects a deliberate control leak, and reports zero production-root
  hits. The wider Runtime privacy/relocation/database band passes `107`. That
  run also exposed and fixed the nullable message-reference migration's
  compatibility with the existing over-advanced-stamp repair path. Restart-
  safe whole-Core account deletion remains the only Step 8 behavior gap; no
  real source deletion, external action, or irreversible cutover occurred.
- 2026-08-14 16:01 MYT - Completed the native authenticated multipart V2
  transfer milestone. Every part shares one Argon/HKDF salt and nonce prefix,
  uses a disjoint monotonically ordered 64-bit nonce block, authenticates its
  exact volume ordinal, and is committed by the encrypted controller written
  last at `core.anima`. Native set import checks the exact directory, ordered
  part names, lengths, hashes, archive/set/capture identity, aggregate record
  uniqueness and payload completeness before returning a create-only staging
  Core; missing, extra, reordered, foreign-set, tampered, truncated, or
  appended inputs fail with staging cleanup. The product export manager now
  routes FAT-like probes through verified controller-last publication, and
  full plus recovery-only imports recognize the controller magic and preflight
  the complete set rather than only the controller file. Native archive tests
  pass `7`, focused transfer/server/API tests pass `79`, Python-feature compile
  and scoped strict Clippy pass, and the paid package workflow remains
  disabled. Backward V1/JSON import and restart-safe whole-Core account
  deletion remain open; no external, destructive, or irreversible action
  occurred.
- 2026-08-14 16:13 MYT - Closed the backward V1/JSON transfer gap. Core
  Transfer now detects encrypted legacy JSON vaults and binary `ANMA`
  capsules, imports them only into the pre-cutover legacy source, clears the
  unlock session, and reports that canonical migration is required. The
  server authenticates cutover state before decrypting or mutating and rejects
  every frozen/validation/pending/forward-only state; V1 JSON schema migration
  and V1 capsule restoration are covered, including a no-mutation post-freeze
  regression. The completed native multipart product path is enabled in the
  desktop instead of remaining visibly gated. Vault tests pass `28`, the
  desktop contract passes `3`, and the production desktop build plus scoped
  Ruff/diff hygiene pass. Steps 2 and 7 are complete; restart-safe whole-Core
  account deletion remains the only local Step 8 behavior gap. The paid
  workflow remains disabled and no external or irreversible action occurred.
- 2026-08-14 16:35 MYT - Completed Step 8's final local behavior gap with
  authenticated restart-only whole-Core account deletion. Scheduling verifies
  the canonical owner, complete active/retained Cores, exact current Runtime
  binding, and absence of competing restart intents, then revokes all unlock
  sessions without touching live storage. Pre-resource startup fails closed
  while the old Runtime process is live; once stopped, an authenticated journal
  drives active-Core, retained-Core, and exact Runtime quarantine/removal,
  registry retirement, credential deletion, and fresh-Core recreation. All
  seven durable crash boundaries resume, tampered intent and live-process cases
  preserve data, zero-based owner IDs work through Soul relocation, and the
  desktop clears decrypted client state before presenting restart guidance.
  Registry/Soul/user tests pass `42`, API-client/desktop contracts pass `34`,
  the desktop production build and full Server Ruff pass, and Step 8 is locally
  complete. No real Core, Runtime, or recovery source was deleted; the paid
  workflow remains disabled and no external or irreversible action occurred.
- 2026-08-14 17:05 MYT - Completed the local implementation for Steps 3 and 5.
  The production API/UI now drives the resumable converter, verified Soul
  relocation, explicit accept/reject, and legacy-routing rollback. Acceptance
  fails without the active relocated Soul, while forward-only startup verifies
  the active Soul and crash-resumably removes only its legacy copy. Pending
  startup prepares or safely refreshes the stopped legacy Runtime recovery
  bundle; the first mutation cannot commit without it, signals a mandatory
  second restart, and blocks later portable writes until fresh Runtime startup
  re-verifies the exact source, refreshes recovery without a gap, and retires
  plaintext. Content-derived source/catalog digests are no longer stored in
  Runtime or exposed by the migration API, and old local journal values are
  scrubbed on resume. The focused server band passes `67`, API-client/desktop
  contracts pass `37`, full Server Ruff and the desktop production build pass.
  Step 4 remains deliberately unexecuted behind the mandatory signed-package
  evidence gate; the paid workflow stays disabled and no real source, marker,
  external action, or irreversible operation occurred.
- 2026-08-14 17:16 MYT - Completed Step 9's architecture reconciliation.
  The whitepaper, Portable Core/three-tier theses, architecture index,
  memory/document/source/database references, PRD amendment, and canonical
  filesystem graph now describe Soul + CoreFS as portable authority and
  PostgreSQL/device/grant/credential state as machine-local. The docs record
  implemented domain routes, exact paths, key purposes, reversible
  migration/rejection, restart-fenced first write, fresh Runtime/Soul
  retirement, V2 single/multipart transfer, degraded recovery, and restart-only
  activation/rollback/deletion. Obsolete Runtime-inside-Core, JSONL authority,
  Runtime original/source authority, plaintext index, and impossible
  discarded-key forward-secrecy claims were removed. The canonical filesystem
  page deliberately retains planned status until the signed-package and
  irreversible acceptance gate passes. Repository organization and diff
  hygiene pass; the paid workflow remains disabled and no external action or
  real cutover occurred.
- 2026-08-16 02:57 MYT - Completed every feasible local portion of Steps 10
  and 11. The focused server authority/transfer matrix passes `211`, desktop
  transfer contracts pass `3`, native capsule/archive filters pass `15` and
  `7`, the complete desktop suite passes `371`, and the complete server suite
  passes `3703` with `6` environment skips. Root lint/build, a temporary
  Alembic upgrade to head `20260812_0001`, isolated temporary-Core `/health`,
  repository organization, and diff hygiene pass. Full-suite stress exposed
  and fixed a CoreFS rebuild-worker/Runtime-engine teardown race: lifecycle
  shutdown now drains workers before engine disposal, scheduling is quiesced
  during teardown, and concurrent test traffic uses per-test file-backed
  SQLite connections. PCF-008 remains `in_progress` only for the mandatory
  cost-deferred signed MSI/PKG/DEB/RPM evidence and explicitly authorized
  live/irreversible Step 4 and smoke operations. The paid workflow remains
  disabled; no real first write, source deletion, Core pointer swap, recovery
  activation, external publication, or user-state mutation occurred.

- 2026-08-16 15:34 MYT - User approved a greenfield first-release contract:
  no supported user or installation predates Portable Core. PCF-008 now removes
  unreleased SQLCipher/PostgreSQL/browser/transcript migration, compatibility
  reads, legacy Runtime/Soul recovery, V1/JSON/capsule import, and the paid
  packaged legacy-writer cleanup gate. Current-format crash recovery,
  authenticated V2 transfer, replacement rollback, retention, and crypto
  checks remain required. PR #142 was safely reduced to PCF-004 and draft PRs
  #143-#147 now form the verified ticket stack; every prior head was preserved
  before the exact force-with-lease update.
- 2026-08-16 16:54 MYT - Implemented and validated the greenfield amendment
  on the top stack branch. First-release bootstrap now activates authenticated
  immutable CoreFS authority before publishing an unlock session, uses the
  canonical Soul path directly, and rejects pre-release manifests. Removed the
  paid workflow, package/install writer census, plaintext draft migration,
  legacy Runtime/Soul/cutover orchestration, and V1 vault API/client/UI. The
  resulting top diff is deletion-heavy (about `16.8k` removed versus fewer
  than `1k` added),
  while current-format V2 transfer, scoped recovery, retained-Core rollback,
  and crash recovery remain. A scoped-transfer regression now proves Soul-only
  archives omit CoreFS authority while FS archives retain it.
- 2026-08-16 16:56 MYT - Committed the reviewed greenfield implementation as
  `0981463f` (`corefs: establish greenfield authority`) and completed plan Step
  12 locally. The ticket remains `in_progress` for Step 11 safe temporary
  smoke/initiative closeout; push and PR metadata update are the user-
  authorized publication actions next.
- 2026-08-23 07:40 MYT - Ran the deferred Step 11 full validation against the
  merged stack (PRs #142-#147, squash-merged to `main` 2026-08-22 as
  `1a99fe0b`) and fixed everything it exposed. The complete server suite had
  last run before the greenfield amendment; on merged main it failed `178`
  tests with two production root causes: greenfield session bootstrap
  hard-failed Soul-only sessions (breaking Soul-scoped password change and the
  retained legacy-credential upgrade), and presence/consent readers let
  `AuthorityStateError` escape when no first-release manifest exists. Also
  fixed: the eval-gated transcript import was rejected on every greenfield
  account (explicit `eval_import` bypass, documented against the disposable
  `ANIMA_EVAL_RESET_ENABLED` boundary); canonical chat history listed every
  thread instead of the active one, so clear/reset never emptied the desktop
  pane; canonical attachment metadata dropped the original filename. Stale
  tests pinning removed pre-release behavior were rewritten to the greenfield
  contract (runtime never adopts a Core-internal `pg_data`; canonical
  reference-only runtime rows; `authoritative` marker state; canonical Soul
  path), store-lifecycle unit tests now stub content-authority reconciliation,
  three retrieval tests no longer write real index files into the repository
  root, and a new committed release smoke (`test_pcf008_greenfield_smoke.py`)
  drives one continuous journey: health, register/unlock, diary/tasks/
  presence/threads, relock/re-login persistence, full V2 export and verified
  same-volume import staging, Soul-only `filesystem_missing` and CoreFS-only
  `recovery_only` staging, and the closed V1 reattachment boundary.
- 2026-08-23 09:00 MYT - Addressed both PR #148 current-head Codex P1
  findings. A Core-level authority gate now makes every content family fail
  closed for sessions without CoreFS capability once the manifest is
  authoritative (route predicates, both legacy mutation guards, and background
  legacy skips return one stable 409 through a shared
  `CoreFsAuthorityUnavailable` handler), so the retained legacy-credential
  upgrade window can no longer fork legacy diary/task/asset/conversation
  state. Presence/consent reads distinguish a never-activated environment
  (legacy fallback with initiative gates defaulting off) from an unparseable
  manifest, which now fails closed. Both fixes carry focused regressions and
  the full server suite passes after the round.
- 2026-08-23 09:30 MYT - Addressed the second-pass Codex P1: the authority
  module now latches every manifest path the process has read or created, so
  a manifest that disappears after observation fails closed instead of being
  read as a never-activated environment that would reopen legacy branches and
  legacy consent defaults mid-process. The presence regression covers
  observe/delete/fail-closed, and the complete server suite passes on the
  final head.
- 2026-08-23 10:20 MYT - Addressed the third-pass Codex P1 pair. The authority
  latch now records the irreversible authoritative state per manifest path, so
  an in-place parseable downgrade (an empty object, a missing release field, or
  an older pending record) fails closed instead of reopening legacy branches
  and legacy consent defaults. Asset, document, and knowledge reads route
  through one fail-closed accessor, so a session that cannot serve canonical
  assets on an activated Core can no longer fall back to Runtime rows and
  plaintext files and resurface content canonical state superseded or deleted;
  the locked case returns the same stable 409. Both carry regressions,
  including one proving a still-unlocked canonical session keeps serving reads
  normally.
- 2026-08-23 11:10 MYT - Addressed the fourth-pass Codex P1 trio, which shared
  one root cause: activation itself now latches authority at the single
  `_write_record` choke point rather than relying on a later read, and all
  nineteen route branches that tested `asset_authority_selection` directly
  now use a gated `asset_corefs_authority_active` predicate. That closes the
  PDF upload writing a plaintext file before its workflow and the avatar
  endpoint falling through to the legacy directory, which the earlier
  selector-only fix had missed. Regressions now cover the activation-write
  latch and prove an FS-locked upload creates no plaintext file and cannot
  serve legacy avatar bytes.
- 2026-08-23 11:50 MYT - Addressed the fifth-pass Codex P1: startup now
  carries the authority state it already loaded into the latch, so a restart
  against an already-activated Core is protected before its first
  authority-dependent request rather than only after one. A regression
  activates a manifest, restarts through `ensure_core_manifest()`, replaces
  the manifest with parseable non-authoritative JSON with no intervening
  authority read, and proves the read fails closed.
- 2026-08-23 12:30 MYT - Addressed the sixth-pass Codex P1, a lock-order
  inversion introduced by the previous round's own fix: startup held the
  manifest lock while taking the authority lock, while authority writes take
  them in the opposite order, so a concurrent login and account discovery
  could deadlock and pin both process-wide locks. The startup latch now runs
  after the manifest lock is released, and a scan confirmed no other
  manifest-update callback reaches authority code. The regression forces the
  exact interleaving with events and was verified red (blocked ~60s) against
  the reintroduced inversion before passing green.

## Validation

- Commands:
  - Step 11 full validation on merged main (2026-08-23):
    `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test` — complete server suite
    `3539 passed, 6 skipped, 0 failed` after the recorded fixes (the first run
    on the merged tree failed `178`); `bun run test:desktop` (`346 passed`);
    `cargo test -p anima-corefs` (complete native suite incl. integration/doc
    tests); `cargo test -p anima-core core_archive` (`7 passed`);
    `cargo check -p anima-corefs -p anima-core -p desktop --all-targets`;
    root `bun run build` (server + desktop + animus); `bun run lint`;
    `bun run check:repo`; `git diff --check`; isolated temporary database
    Alembic upgrade + current at core head `20260812_0001`; committed release
    smoke `apps/server/tests/test_pcf008_greenfield_smoke.py` (`1 passed`,
    full journey).
  - greenfield replacement validation: server authority/transfer/registry/
    preference/security modules (`44 passed`), rewritten diary/document/
    knowledge/task/thread/user API modules (`19 passed`), state inventory
    (`7 passed`), and the earlier archive/preparation band through every
    changed case before its unchanged 100-MiB stress case (`57 passed`)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/corefs-transfer.test.ts apps/desktop/tests/api-auth.test.ts apps/desktop/tests/settings-storage-classification.test.ts`
    (`36 passed`)
  - desktop TypeScript, `cargo check -p desktop --all-targets`, CoreFS logical
    mutation tests (`8 passed`), anima-core V2 archive tests (`7 passed`), and
    `cargo check -p anima-corefs -p anima-core --all-targets` (passed)
  - scoped/full Server Ruff, Rust format, `bun run build`, repository
    organization, and `git diff --check` (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_cutover.py apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_transfer_api.py apps/server/tests/test_corefs_api.py apps/server/tests/test_corefs_logical.py apps/server/tests/test_corefs_security_api.py apps/server/tests/test_corefs_soul_relocation.py apps/server/tests/test_corefs_orchestration.py apps/server/tests/test_corefs_legacy_runtime_recovery.py apps/server/tests/test_vault.py apps/server/tests/test_health_integration.py -q`
    (`211 passed`)
  - `bun test apps/desktop/tests/corefs-transfer.test.ts` (`3 passed`)
  - `cargo test -p anima-core capsule` (`15 passed`)
  - `cargo test -p anima-core core_archive -q` (`7 passed`)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false bun run test`
    (`3703 passed, 6 skipped`; complete server suite)
  - `bun run test:desktop` (`371 passed`)
  - `bun run lint` and `bun run build` (passed)
  - temporary `bun run db:server:current` after upgrading an isolated database
    (`20260812_0001 (head)`)
  - isolated temporary Core/Runtime startup with in-memory test credentials and
    `GET /health` (`200`, `status=ok`; no real Core or credential-store access)
  - focused rebuild-drain/migration/creation/knowledge/security/Runtime
    regressions after the teardown-race repair (passed)
  - `bun scripts/check-repo-organization.ts` and `git diff --check` (passed)
  - `ANIMA_CORE_REQUIRE_ENCRYPTION=false uv run pytest apps/server/tests/test_corefs_cutover.py apps/server/tests/test_corefs_indexer.py apps/server/tests/test_dev_session_continuity.py -q` (`82 passed`)
  - `cargo test -p anima-corefs` (complete native suite passed)
  - `cargo clippy -p anima-corefs --all-targets -- -D warnings` (passed)
  - `PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 cargo check -p anima-core --features python` (passed)
  - scoped Ruff check/format, Rust format, and `git diff --check` (passed)
  - `uv run pytest -q apps/server/tests/test_knowledge_api.py apps/server/tests/test_corefs_knowledge_sources.py apps/server/tests/test_corefs_assets.py apps/server/tests/test_image_assets.py apps/server/tests/test_image_deletion.py apps/server/tests/test_html_ingestion.py apps/server/tests/test_structured_document.py apps/server/tests/test_web_fetch.py` (`99 passed`)
  - `uv run pytest -q apps/server/tests/test_knowledge_api.py apps/server/tests/test_okf_import_export.py apps/server/tests/test_corefs_knowledge_sources.py apps/server/tests/test_knowledge_autocompile.py` (`61 passed`)
  - `uv run pytest -q apps/server/tests/test_corefs_runtime_privacy.py apps/server/tests/test_corefs_legacy_runtime.py apps/server/tests/test_runtime_db.py -k 'not test_external_runtime_database_url and not test_runtime_database_url'` (`107 passed`)
  - `cargo test -p anima-core core_archive` (`7 passed` after authenticated
    multipart set coverage)
  - `uv run pytest apps/server/tests/test_corefs_archive_transfer.py apps/server/tests/test_corefs_transfer.py apps/server/tests/test_corefs_transfer_api.py -q`
    (`79 passed` after multipart product/import integration)
  - `uv run pytest apps/server/tests/test_vault.py -q` (`28 passed` after V1
    JSON/capsule compatibility and post-freeze no-mutation coverage)
  - `bun test apps/desktop/tests/corefs-transfer.test.ts` (`3 passed` after
    legacy import and multipart UI activation)
  - `bun run build:desktop` (passed)
  - `uv run pytest apps/server/tests/test_corefs_active_core_registry.py apps/server/tests/test_corefs_instance_registry.py apps/server/tests/test_corefs_soul_relocation.py apps/server/tests/test_users.py -q`
    (`42 passed` after restart-only deletion, live Runtime exclusion, registry
    retirement, fresh-Core recreation, and every durable crash boundary)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/corefs-transfer.test.ts`
    (`34 passed`)
  - `bun run --cwd apps/desktop build` (passed)
  - `bun run lint:server` (passed)
  - `uv run pytest apps/server/tests/test_corefs_soul_relocation.py apps/server/tests/test_corefs_legacy_runtime_recovery.py apps/server/tests/test_corefs_orchestration.py apps/server/tests/test_corefs_logical.py apps/server/tests/test_corefs_transfer_api.py -q`
    (`67 passed` after production migration decisions, Soul rollback/retirement,
    restart-bundle refresh, and first-write restart fencing)
  - `bun test packages/api-client/tests/client.test.ts apps/desktop/tests/corefs-transfer.test.ts apps/desktop/tests/api-auth.test.ts`
    (`37 passed`)
  - `bun run build:desktop` and `bun run lint:server` (passed)
  - `bun scripts/check-repo-organization.ts` after Step 9 architecture
    reconciliation (passed)
  - `git diff --check` after Step 9 (passed)
  - `env PYO3_PYTHON=.venv/bin/python cargo check -p anima-core --features python`
    (passed)
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
  - `uv run pytest -q --tb=short apps/server/tests/test_diary_api.py apps/server/tests/test_corefs_diary_migration.py`
    after canonical diary/folder/draft routing (`51 passed`)
  - `uv run pytest -q --tb=short apps/server/tests/test_diary_api.py apps/server/tests/test_corefs_diary_migration.py apps/server/tests/test_corefs_assets.py apps/server/tests/test_corefs_document_migration.py`
    after the canonical diary binary-attachment adapter (`59 passed`)
  - scoped Ruff check/format and `git diff --check` after the diary binary-
    attachment adapter (passed)
  - `uv run pytest -q --tb=short apps/server/tests/test_users.py::test_post_cutover_account_profile_never_mutates_legacy_user`
    after canonical agent-avatar routing (`1 passed`)
  - `uv run pytest -q --tb=short apps/server/tests/test_users.py apps/server/tests/test_agent_biography_preview.py apps/server/tests/test_creation_flow.py apps/server/tests/test_corefs_assets.py`
    (`39 passed`)
  - `uv run pytest -q --tb=short apps/server/tests/test_users.py::test_post_cutover_account_profile_never_mutates_legacy_user apps/server/tests/test_corefs_assets.py`
    after normalizing native asset-mutation failures (`6 passed`)
  - scoped Ruff check and `git diff --check` after canonical agent-avatar
    routing (passed)
  - `uv run pytest -q --tb=short apps/server/tests/test_chat_attachments.py apps/server/tests/test_chat_image_assets.py apps/server/tests/test_image_deletion.py apps/server/tests/test_threads_api.py apps/server/tests/test_corefs_conversation_migration.py`
    after canonical chat-image routing (`47 passed`)
  - scoped Ruff check and `git diff --check` after canonical chat-image routing
    (passed)
  - `uv run pytest -q --tb=short apps/server/tests/test_documents_api.py apps/server/tests/test_document_store.py apps/server/tests/test_pdf_workflow_checkpoints.py apps/server/tests/test_document_reparse.py apps/server/tests/test_corefs_document_migration.py apps/server/tests/test_corefs_assets.py`
    after canonical document upload/registration (`104 passed`)
  - focused post-cutover document authority rerun (`1 passed`)
  - scoped Ruff check and `git diff --check` after canonical document routing
    (passed)
  - direct Python-enabled anima-core unit-test linking remains unavailable on
    this macOS extension-module host because Python symbols are not linked;
    the same binding compiles, while its transaction behavior is covered in
    anima-corefs and its Python authority/request behavior is covered by the
    server band above
- Changed paths:
  - greenfield amendment: `apps/server/src/anima_server/services/corefs/{authority.py,greenfield.py,soul_store.py}`; session/keyslot/domain authority integration; native logical activation; current-format transfer/API/client/UI; removal of pre-release cutover/orchestration/legacy Runtime/Soul relocation, V1 vault, browser draft cleanup, installer/package scripts, paid workflow, and their tests
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
  - `apps/server/src/anima_server/services/corefs/migration.py`
  - `apps/server/src/anima_server/main.py`
  - `apps/server/tests/{conftest.py,test_corefs_migration.py,test_dev_session_continuity.py,test_security_hardening.py}`
  - `apps/desktop/tests/desktop-release-contract.test.ts`
  - `docs/architecture/system/portable-state-inventory.md`
  - `packages/anima-corefs/src/logical/{mod.rs,mutation.rs,mutation/executor.rs,mutation/tests.rs}`
  - `packages/anima-core/src/ffi.rs`
  - `apps/server/src/anima_server/{schemas/corefs.py,api/routes/corefs.py}`
  - `apps/server/src/anima_server/services/corefs/{logical.py,cutover.py}`
  - `apps/server/tests/{test_corefs_api.py,test_corefs_logical.py,test_corefs_cutover.py}`
  - `apps/server/tests/test_corefs_recovery_access.py`
  - `packages/api-client/src/types.ts`
  - `apps/server/src/anima_server/services/corefs/active_core_registry.py`
  - `apps/server/src/anima_server/services/corefs/instance_registry.py`
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
  - `apps/server/src/anima_server/schemas/users.py`
  - `apps/server/tests/test_users.py`
  - `apps/desktop/src/pages/Profile.tsx`
  - `apps/server/src/anima_server/services/corefs/{conversation_authority.py,conversation_mutations.py}`
  - `apps/server/src/anima_server/api/routes/{chat.py,threads.py}`
  - `apps/server/src/anima_server/api/routes/images.py`
  - `apps/server/src/anima_server/services/agent/attachments.py`
  - `apps/server/src/anima_server/api/routes/documents.py`
  - `apps/server/src/anima_server/services/documents/store.py`
  - `apps/server/src/anima_server/services/ingestion/{sources.py,adapters/documents.py}`
  - `apps/server/tests/test_documents_api.py`
  - `apps/server/tests/{test_corefs_conversation_migration.py,test_threads_api.py}`
  - `apps/server/src/anima_server/services/agent/{persistence.py,service.py}`
  - `apps/server/src/anima_server/models/runtime.py`
  - `apps/server/alembic_runtime/versions/035_corefs_message_references.py`
  - `packages/anima-file-tools/src/patch/parser.rs`
  - `packages/anima-file-tools/tests/patch.rs`
  - `packages/anima-corefs/src/logical/{mutation.rs,mutation/patch.rs,mutation/tests.rs}`
  - `packages/anima-core/src/ffi.rs`
  - `apps/server/src/anima_server/services/corefs/{writing_authority.py,writing_mutations.py,diary_migration.py}`
  - `apps/server/src/anima_server/api/routes/diary.py`
  - `apps/server/tests/test_diary_api.py`
  - `apps/server/src/anima_server/services/corefs/{asset_authority.py,asset_mutations.py}`
  - `apps/server/tests/{test_corefs_assets.py,test_corefs_document_migration.py}`
  - `apps/server/src/anima_server/{services/vault.py,schemas/vault.py}`
  - `apps/server/tests/test_vault.py`
  - `packages/api-client/src/types.ts`
  - `apps/desktop/src/pages/settings/CoreTransferSettings.tsx`
  - `apps/desktop/tests/corefs-transfer.test.ts`
- Notes:
  - PCF-001 through PCF-007 are done. The 2026-08-16 greenfield amendment
    removes the pre-release signed-package writer-exclusion gate; ordinary
    release signing remains outside PCF-008.
  - The macOS host compiles the Python-enabled PyO3 binding; native authority
    transaction behavior is covered in Rust and the installed extension is
    exercised through the greenfield server authority/API tests.
  - Step 11 completed 2026-08-23 against the merged stack: complete server,
    desktop, and native suites plus root lint/build, repository organization,
    an isolated Alembic upgrade to core head `20260812_0001`, and the committed
    continuous-journey release smoke all pass. No paid cross-version workflow
    or legacy fixture was required, and no real user Core or external system
    was mutated. Greenfield-unreachable legacy code paths found during the
    closeout review are recorded on PCF-009 for gated retirement.
