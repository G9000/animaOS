---
title: Local Runtime Daemon Control Contract
description: Explicit lifecycle and control API contract for daemon-backed local runtime
category: architecture
---

# Local Runtime Daemon Control Contract

[Back to Architecture Index](../README.md)

## Purpose

Desktop and future tooling must share one control contract for local lifecycle operations. This contract is intentionally local-only and separate from online API authentication.

## Assumptions

- Control API is bound to localhost or equivalent OS-private channel by default.
- Desktop UI and daemon run under the same local user by default.
- No secret material, passphrases, raw DEKs, or long-lived memory payloads are exchanged through this contract.

## Base API Surface

| Endpoint | Method | Purpose | Success Response |
| --- | --- | --- | --- |
| `/api/v1/runtime-daemon/status` | GET | Read daemon state and lock policy | [`DaemonHealthResponse`](#response-shapes) |
| `/api/v1/runtime-daemon/start` | POST | Start supervised Python runtime | [`DaemonCommandResponse`](#response-shapes) |
| `/api/v1/runtime-daemon/stop` | POST | Stop supervised Python runtime | [`DaemonCommandResponse`](#response-shapes) |
| `/api/v1/runtime-daemon/restart` | POST | Stop then start supervised runtime | [`DaemonCommandResponse`](#response-shapes) |
| `/api/v1/runtime-daemon/lock` | POST | Transition to secure-locked background policy | [`DaemonCommandResponse`](#response-shapes) |
| `/api/v1/runtime-daemon/unlock` | POST | Exit lock state and allow background jobs | [`DaemonCommandResponse`](#response-shapes) |
| `/api/v1/runtime-daemon/logs` | POST | Retrieve runtime log snapshot | [`DaemonOpenLogsResponse`](#response-shapes) |

## Runtime State Machine

States are canonical and persisted by daemon heartbeat:

- `stopped`
- `starting`
- `ready`
- `degraded`
- `locked`
- `stopping`
- `failed`

Transitions:

- `stopped` -> `starting` on `start`/`restart`
- `starting` -> `ready` on successful runtime health check
- `starting` -> `failed` on repeated health check failures
- `ready` -> `degraded` on bounded intermittent runtime faults
- any non-terminal state -> `stopping` on `stop`
- `stopping` -> `stopped` on normal exit
- `ready|degraded` -> `locked` on lock handoff from unlocked state
- `locked` -> `ready|degraded` on unlock handoff
- any state -> `failed` on unrecoverable internal faults

## Security and Local Auth

Daemon must enforce at least one of:

1. Local bind + short-lived random daemon token passed in `x-anima-daemon-token` header.
2. OS-account-gated local IPC channel.
3. Explicit allowlist for trusted executables.

Hard requirements:

- Control endpoints reject requests without valid local auth except `status` if policy marks it safe.
- Token is not logged and not persisted in plaintext.
- `status` responses may omit sensitive internals if requested identity lacks admin scope.

## Error and Retry Contract

- Use stable error shape:
  - `code`: short machine-readable identifier.
  - `category`: `transient | dependency | permission | policy | validation | internal`.
  - `message`: user-safe message.
  - `hint`: optional actionable suggestion.
  - `retryable`: boolean.
  - `retryAfterSeconds`: optional cooldown window when retryable.
- Recommended client behavior:
  - Retry transient commands with exponential backoff.
  - Do not retry `permission` or `validation` categories.
- Retry envelope defaults:
  - `maxAttempts`: 5
  - base delay: 500ms
  - max delay: 5000ms
  - jitter: up to 25%

## Lock/Unlock Policy Shape

- `lock` MUST keep runtime process alive unless explicit policy disables background mode.
- `locked` state MUST pause/disable jobs needing decrypted user context.
- `unlock` SHOULD restore background mode based on explicit desktop preference and user unlock handoff.
- Desktop UI closing event does not implicitly toggle lock.

## Response Shapes

All types are shared from `@anima/daemon-contracts`:

- `DaemonHealthResponse`
- `DaemonStatusPayload`
- `DaemonLockPayload`
- `DaemonLocalAuthPolicy`
- `DaemonCommandResponse`
- `DaemonOpenLogsResponse`
- `DaemonClientIdentity`

## Request Shapes

- `DaemonStartRequest`: optional `source`, `backgroundMode`, `preferReadyWithinMs`.
- `DaemonStopRequest`: optional `force`, `timeoutMs`.
- `DaemonRestartRequest`: optional `force`, `backgroundMode`.
- `DaemonLockRequest`: optional `source`, `reason`, `pauseBackgroundJobs`.
- `DaemonUnlockRequest`: optional `source`, `handoffToken`.
- `DaemonOpenLogsRequest`: optional `tailLines`, `follow`.

## Observability

Status responses SHOULD include:

- Runtime PID/path/ports when available.
- Last health-check timestamps.
- Health summary and lock summary.

Logs should support local tail snapshot and avoid leaking secrets.
