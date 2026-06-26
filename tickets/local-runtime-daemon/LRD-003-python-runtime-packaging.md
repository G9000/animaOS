# LRD-003 - Package Python runtime artifact

- Status: backlog
- Priority: P1
- Scope: server packaging
- Parent: `LRD-000`
- Depends on: `LRD-001`
- Owner: unassigned
- PRD: none
- Plan: docs/superpowers/plans/2026-06-26-local-runtime-daemon.md
- Created: 2026-06-26 17:06 MYT
- Updated: 2026-06-26 17:18 MYT
- Started:
- Completed:

## Goal

Create a production packaging strategy for the Python FastAPI runtime that the Rust daemon can supervise.

## Deliverables

- Decide packaging toolchain for server artifact
- Compare packaging options: PyInstaller, Nuitka, uv-managed embedded venv, and bundled Python
- Define bundled dependencies and runtime config
- Define how migrations and embedded PostgreSQL startup work in packaged mode
- Document artifact location expected by daemon

## Acceptance

- Packaging decision matrix records tradeoffs and chosen default
- Normal users do not need `uv`, `bun`, or terminal commands
- Daemon can locate and start the runtime artifact
- Runtime still owns cognition, memory, tools, SQLCipher, and database migrations

## Activity Log

- 2026-06-26 17:06 MYT - Ticket created.

## Validation

- Commands:
  - not run yet
- Changed paths:
  - none
- Notes:
  - backlog ticket only
