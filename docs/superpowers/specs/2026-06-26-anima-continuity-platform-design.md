# Anima Continuity Platform Design

Date: 2026-06-26
Status: Proposed
Author: Codex

## Summary

Anima should evolve from a local-first single-device companion into a hybrid continuity platform. The product promise is:

- Never lose your Anima.
- Resume on any device.
- Keep the core self encrypted.

This design preserves the current local-first thesis while adding a cloud continuity layer for backup, restore, sync, device identity, and thread handoff. The cloud is not the primary home of Anima's cognition. It is the coordination and recovery layer around a local encrypted core.

## Product Goal

Create a prosumer-sellable paid layer around the current local-first architecture without discarding the repository's thesis, memory model, or local runtime.

The first commercial wedge is continuity, not generic AI access. Users should pay because Anima is recoverable, portable, and available across devices.

## Non-Goals

- Rewriting Anima into a cloud-only SaaS assistant
- Making third-party channels the primary product surface
- Supporting unrestricted concurrent live turns on the same thread
- Building enterprise admin, multi-tenant policy, or shared workspace features in the first milestone
- Moving all durable memory plaintext into cloud-hosted services

## Product Positioning

Anima becomes two connected products:

1. `Anima Core`
   The local encrypted runtime and durable identity layer. This includes long-term memory, self-model, local thread state, and local cognition.
2. `Anima Continuity Cloud`
   The account, device, backup, sync, sequencing, and lease layer that lets the same Anima survive machine loss and continue across clients.

The continuity cloud is the sellable service. The local core remains the moat.

## Design Principles

1. Local-first remains the default operating mode.
2. Durable identity and memory are more important than runtime convenience.
3. Data may merge. Live execution may not.
4. Cloud convenience must not require abandoning encrypted core ownership.
5. Recovery and continuity must be legible to end users, not hidden backend behavior.

## User Promise

For a paid prosumer user, Anima should provide:

- encrypted cloud backup of the core
- one-click restore onto a new machine
- multi-device sync across official clients
- continuity of thread history and durable context
- explicit handoff when continuing a live thread on another device
- device management and revocation

The first landing-page message should be: `Never lose your Anima.`

## Architecture Overview

The system should be split into three state classes.

### 1. Portable Canonical State

This is the user's core and must be encrypted client-side before sync:

- long-term memory items and evidence
- self-model blocks
- soul, persona, and user directive blocks
- durable thread history
- durable summaries and notes
- encrypted key-wrapping material

This state is portable, authoritative, and continuity-critical.

### 2. Rebuildable Runtime State

This is operational state that may be synced for continuity but can be rebuilt or recovered:

- runtime threads and runs
- compaction artifacts
- pending memory operations
- background task cursors
- runtime caches and retrieval metadata
- notification state

This state can be coordinated by the cloud, but it is not the sacred source of identity.

### 3. Active Execution State

This state must not be freely merged:

- active turn execution on a thread
- tool calls in progress
- approval checkpoints in flight
- streaming response ownership

This state is controlled through leases rather than merge logic.

Rule: `data merges, execution leases`.

## Cloud Services

The first continuity cloud should be a narrow control plane and sync plane, not a full hosted runtime.

### Auth Service

Responsibilities:

- account identity
- session auth
- recovery flows
- billing identity later

### Device Registry

Responsibilities:

- trusted device registration
- device public keys and wrapped keys
- device revoke and recovery eligibility
- last-seen and sync capability tracking

### Sync Service

Responsibilities:

- accept encrypted mutations from devices
- assign monotonic server sequence positions
- serve pull sync for replicas
- validate mutation ordering and lease constraints

### Lease Service

Responsibilities:

- thread-scoped write leases
- heartbeat and expiry
- graceful handoff
- forced takeover for failure recovery

### Blob and Backup Service

Responsibilities:

- encrypted snapshots
- encrypted attachment blobs
- restore manifests
- disaster recovery checkpoints

## Trust Model

Anima should use a hybrid trust model with two explicit zones.

### Zone A: Zero-Knowledge Core

The cloud must not require plaintext access to:

- long-term memory
- self-model content
- durable thread history
- identity artifacts
- user directives

These are encrypted on the client and synced as ciphertext plus limited metadata needed for sequencing and transport.

### Zone B: Optional Trusted Convenience

The cloud may process scoped convenience features when the user opts in:

- hosted jobs
- notifications
- temporary hosted inference
- connector delivery state
- operational analytics that do not require core plaintext

These features must be clearly separated from the encrypted core and must not silently expand cloud trust.

## Key Model

The continuity platform should use per-account recovery and per-device trust.

- each user has a root recovery key
- each device has a device keypair
- data encryption keys are wrapped for authorized devices
- device addition requires explicit authorization from an existing trusted device or a recovery path
- device revocation removes future key access and sync participation

This model supports encrypted continuity without treating the cloud as the owner of the user's mind.

## Phase 2 Recovery Decision

The first milestone must use a concrete recovery mechanism rather than leaving recovery abstract.

Phase 2 recovery should work like this:

- the user creates or is issued a `Recovery Key` during continuity setup
- the Recovery Key is user-held and must be stored by the user outside the product session
- account auth identifies the account and grants access to encrypted backup metadata
- restoring a new device requires either:
  - approval from an already trusted device, or
  - the user supplying the Recovery Key
- the cloud stores wrapped keys and ciphertext but does not hold plaintext core-memory keys

Deferred alternatives such as passkey-only recovery, cloud escrow, or social recovery are out of scope for the first implementation plan.

## Sync Model

Live sync should use structured encrypted events, not raw `.anima/` file replication.

### Sync Layers

1. `Encrypted event log`
   Append-only domain mutations such as `memory_item_added`, `thread_message_appended`, `self_model_updated`, `session_note_written`, and `thread_closed`.
2. `Local materialized state`
   Each device applies events into its own local SQLCipher and runtime storage.
3. `Encrypted snapshots`
   Periodic snapshots accelerate restore and new-device bootstrap. Event replay continues from the latest snapshot position.

### Sync Rules

- devices may queue offline writes locally
- reconnect pushes encrypted pending mutations
- server orders accepted mutations with monotonic sequence numbers
- clients pull and apply ordered mutations
- attachments and large blobs are stored separately and referenced from events
- raw database file sync is used only for cold backup, not for live multi-device continuity

## Lease Model

The system should support multi-device use without concurrent live execution on the same thread.

### Lease Rules

- one thread has one current writer device
- any number of devices may read the thread
- different threads may be active on different devices at the same time
- only the current lease owner may start a new run on that thread
- graceful handoff is the default takeover behavior
- forced takeover is available for stuck or offline devices

### User-Facing Behavior

When a second device opens a thread that is active elsewhere:

- it can read immediately
- it is view-only until it holds the lease
- it can request `Take Over Here`
- if the current owner is healthy, the handoff waits for the current turn to complete
- if the current owner is unavailable, a forced takeover can expire the old lease

This prevents split-brain thread execution while preserving a natural cross-device experience.

## Product Surface and Tiers

### Free / Local Tier

- local-only runtime
- local memory
- manual export and import
- user-managed providers

### Paid Prosumer Continuity Tier

- encrypted backup
- device restore
- multi-device sync
- thread handoff
- device management and revoke
- encrypted attachment backup
- sync health visibility

### Later Upsell Tier

- hosted jobs
- hosted inference
- web client
- mobile client
- third-party channels such as Telegram or WhatsApp

The first paid tier should sell continuity, not generic AI chat access.

## Migration Strategy

Anima should move in phases rather than through a rewrite.

### Phase 1: Continuity-Ready Local Core

- formalize portable canonical state versus rebuildable runtime state
- add stable IDs and versionable domain objects
- emit internal domain events for important state changes
- add export and snapshot primitives beyond raw file copy

### Phase 2: Backup and Restore

- add account auth
- add device registration
- add encrypted snapshot upload and download
- support full user-core restore to a new device

This is the first commercial milestone and the first implementation plan target.

### Phase 3: Incremental Sync

- add encrypted event push and pull
- sync durable memory, thread history, self-model, notes, and settings
- keep active execution local and lease-controlled

### Phase 4: Thread Leases and Handoff

- add per-thread write leases
- add heartbeats and expiry
- add graceful and forced takeover flows
- sync enough runtime state for clean continuation after handoff

### Phase 5: Optional Hosted Convenience

- hosted jobs
- web client
- mobile client
- connectors
- optional hosted inference

## Required Repository Direction

The current local server should evolve from "the product runtime" into "a device runtime node" within a broader continuity system.

Key repository changes implied by this design:

- introduce domain events for durable and runtime mutations
- separate portable canonical state from rebuildable runtime state
- isolate non-mergeable active execution state
- assign sync-friendly identifiers and sequencing metadata
- treat `.anima/` as a local replica and snapshot source, not as the live sync transport

## Risks and Tradeoffs

### Benefits

- preserves the local-first thesis
- creates a clean recurring subscription product
- supports web, mobile, and connectors later without redefining identity
- provides a path to enterprise without starting as enterprise software

### Costs

- adds significant synchronization and key-management complexity
- requires a new cloud service boundary
- forces sharper domain modeling between durable, runtime, and active execution state

### Rejected Alternatives

- `Cloud-hosted Anima`: commercially simpler short term, but collapses the local-first moat
- `Backup-only`: easier to ship, but too weak for strong continuity
- `Full concurrent live-thread merge`: too risky for agent execution, tools, and self-model consistency

## Scope Guard For Planning

This spec defines the target continuity architecture and product direction. The first implementation plan derived from this spec must be limited to:

- backup and restore foundation
- account and device registration
- encrypted snapshot transport
- restore UX and recovery path

Live incremental sync, lease handoff, hosted jobs, and third-party channels are intentionally out of scope for the first implementation plan.

## Phase 2 Restore Matrix

The first milestone must restore the continuity-critical core without requiring a bit-for-bit recovery of the entire local runtime.

| State Class | Backed Up in Phase 2 | Restored in Phase 2 | Rebuilt Locally | Out of Scope for Phase 2 |
|-------------|----------------------|---------------------|-----------------|--------------------------|
| Portable canonical state | Yes | Yes | No | No |
| Rebuildable runtime state | No | No | Yes | No |
| Active execution state | No | No | No | Yes |

Phase 2 therefore means:

- long-term memory, self-model, encrypted thread history, directives, and other portable canonical state are restored on the new device
- encrypted attachment blobs referenced by restored canonical thread history are restored with that history
- runtime runs, transient queues, retrieval caches, and similar operational state are reinitialized locally
- in-flight turns, active tool calls, streaming ownership, and approval checkpoints are not recoverable in the first milestone

This scope keeps the first paid promise honest: users do not lose Anima, but they may lose transient live execution when restoring onto a new device.

## Success Criteria

This design is successful if it enables a first paid product with all of the following properties:

- a user can lose a machine without losing Anima
- a new device can restore the user's encrypted core
- the architecture remains compatible with later multi-device continuity
- the local-first thesis remains intact
- future live sync can layer on without requiring a full storage rewrite
