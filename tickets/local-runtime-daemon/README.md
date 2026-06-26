# Local Runtime Daemon

This folder tracks the local ticket backlog for making Anima run as a supervised local runtime independently of the desktop UI window.

Agents claiming work in this folder should follow [prd-ticket-workflow.md](../../docs/ops/prd-ticket-workflow.md).

Parent tracker: [LRD-000-parent.md](./LRD-000-parent.md)

## Order

1. `LRD-001` Define daemon lifecycle and control contract
2. `LRD-006` Define lock/unlock and background job policy
3. `LRD-009` Create local daemon threat model
4. `LRD-002` Scaffold Rust daemon binary
5. `LRD-003` Package Python runtime artifact
6. `LRD-004` Add daemon health, logs, and restart policy
7. `LRD-005` Integrate desktop with daemon controls
8. `LRD-007` Add OS autostart/service installation
9. `LRD-008` Add release packaging pipeline

## Done Condition

- Desktop UI can close without killing the local runtime when background mode is enabled.
- Runtime lifecycle is explicit and controllable from desktop.
- Normal users do not need `bun dev`, terminal commands, or Docker.
- Docker remains reserved for self-hosted/server deployment.
