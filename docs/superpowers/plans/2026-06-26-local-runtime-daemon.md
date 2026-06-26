# Local Runtime Daemon

This is the local planning artifact for making Anima survive desktop UI close without requiring users to run `bun dev` or Docker.

## Project

- Name: Local Runtime Daemon
- Team: ANIMA Core
- Target delivery: desktop production packaging
- Owner: You
- State model: Backlog -> In Progress -> Done
- Start date: 2026-06-26
- Primary value: Anima keeps running locally as a supervised runtime service while the desktop app acts as a UI/control surface.

## Technical Decision

- Use Rust for the local daemon/supervisor.
- Keep Python/FastAPI as the cognitive runtime.
- Use Docker only for self-hosted/server deployment, not normal desktop packaging.
- Keep `bun dev` as a developer workflow only.

## Architecture Decisions

- The daemon is a supervisor and control plane, not a rewrite of the agent runtime.
- The daemon owns lifecycle, health, local IPC/control auth, logs, ports, restart policy, and autostart.
- The Python server owns cognition, memory, tools, and persistence.
- The desktop UI can close without killing the runtime unless the user explicitly quits/stops background mode.
- Background jobs must respect core lock state and must not keep decrypted secrets alive without explicit policy.

## Ticket Set

Use local ticket IDs `LRD-001` through `LRD-009`.

Parent tracker: `tickets/local-runtime-daemon/LRD-000-parent.md`

| Ticket | Title | Priority | Type | Scope | Notes |
| --- | --- | --- | --- | --- | --- |
| LRD-001 | Define daemon lifecycle and control contract | P1 | Design | daemon + desktop | Define states, local auth, commands, health response, and error model. |
| LRD-002 | Scaffold Rust daemon binary | P1 | Feature | Rust | Add daemon crate/binary with lifecycle shell and local control endpoint or IPC. |
| LRD-003 | Package Python runtime artifact | P1 | Feature | server packaging | Decide and implement packaging for the FastAPI runtime the daemon supervises. |
| LRD-004 | Add daemon health, logs, and restart policy | P1 | Feature | daemon | Add health checks, crash handling, logs, PID/port tracking, and backoff. |
| LRD-005 | Integrate desktop with daemon controls | P1 | Feature | desktop | Desktop reads daemon status and can start/stop/restart/open diagnostics. |
| LRD-006 | Define lock/unlock and background job policy | P1 | Security | daemon + server | Define what keeps running when UI closes and when the core is locked. |
| LRD-007 | Add OS autostart/service installation | P2 | Packaging | installer | Add Windows/macOS/Linux install/start-on-login strategy. |
| LRD-008 | Add release packaging pipeline | P2 | Packaging | release | Ensure installers include daemon, runtime artifact, config, and migration notes. |
| LRD-009 | Create local daemon threat model | P1 | Security | daemon + desktop | Enumerate local IPC, sidecar nonce, unlock, logging, autostart, and background-job risks. |

## Execution Order

1. LRD-001, LRD-006, LRD-009
2. LRD-002, LRD-003
3. LRD-004, LRD-005
4. LRD-007, LRD-008

## Done Criteria

- User can close the desktop window without killing Anima runtime when background mode is enabled.
- Runtime can be stopped intentionally from desktop controls.
- Local control surface is authenticated or IPC-restricted.
- Runtime restarts on crash with bounded backoff.
- Packaged app does not require `bun dev`, terminal commands, or Docker for normal desktop use.
- Docker remains documented as a separate self-hosted/server deployment option.
