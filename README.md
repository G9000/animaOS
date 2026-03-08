# anima-os-lite

Bun workspace monorepo for ANIMA:

- `apps/api`: Bun + Hono backend
- `apps/desktop`: Tauri + React desktop client
- `docs/`: project docs
- `memory/`: local user memory data (git-ignored)

## Requirements

- Bun `>=1.x`
- Rust toolchain (for Tauri packaging/runtime)

## Quick Start

```bash
bun install
bun dev
```

Run app-specific dev tasks:

```bash
bun --filter api dev
bun --filter desktop dev
bun --filter desktop tauri dev
```

## Common Commands

From repo root unless noted:

- `bun dev`: run workspace dev scripts
- `bun run build`: build all workspaces
- `bun run db:push`: apply API DB migrations
- `bun run db:studio`: open Drizzle Studio
- `cd apps/api && bun run test`: run API tests
- `cd apps/api && bun run test:brief`: brief-agent tests

## Repository Notes

- `memory/` is intentionally local-only and fully git-ignored.
- Auth is local-owner bootstrap (no mandatory email identity flow).
- User backup/sync uses encrypted vault export/import with a user passphrase (argon2id + AES-256-GCM).
- Keep prompts in `apps/api/prompts/*.md` and load via `renderPromptTemplate(...)`.
- Use `apps/api/src/lib/task-date.ts` as the shared due-date logic source.

## Docs

- [`docs/whitepaper.md`](docs/whitepaper.md)
- [`docs/roadmap.md`](docs/roadmap.md)
- [`docs/build-release.md`](docs/build-release.md)
