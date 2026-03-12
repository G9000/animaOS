# anima-os-lite

Mixed monorepo for ANIMA:

- `apps/server`: Python + FastAPI backend managed with `uv`
- `apps/desktop`: Tauri + React desktop client orchestrated via `nx`
- `apps/api`: legacy Bun + Hono backend kept during migration
- `docs/`: project docs
- `memory/`: local user memory data (git-ignored)

## Requirements

- Bun `>=1.x`
- Python `>=3.12`
- `uv >=0.9.x`
- Rust toolchain (for Tauri packaging/runtime)

## Quick Start

```bash
bun install
uv sync --all-packages
bun run db:server:up
bun run db:server:upgrade
bun run dev
```

Run app-specific dev tasks:

```bash
bun run dev:server
bun run dev:desktop
bun run dev:api:legacy
```

## Common Commands

From repo root unless noted:

- `bun run dev`: run the Python server and desktop app through `nx`
- `bun run build`: build `apps/server` and `apps/desktop`
- `bun run lint`: lint `apps/server` and type-check `apps/desktop`
- `bun run test`: run Python backend tests
- `bun run python:sync`: sync the `uv` workspace
- `bun run db:server:up`: start local Postgres for `apps/server`
- `bun run db:server:down`: stop the local Postgres service
- `bun run db:server:logs`: tail local Postgres logs
- `bun run db:server:revision -- "<message>"`: create an autogen Alembic revision for `apps/server`
- `bun run db:server:heads`: show Alembic heads for `apps/server`
- `bun run db:server:current`: show current Alembic revision for `apps/server`
- `bun run db:server:upgrade`: apply server migrations to head
- `bun run db:push`: apply legacy API DB migrations
- `bun run db:studio`: open legacy Drizzle Studio

## Repository Notes

- `memory/` is intentionally local-only and fully git-ignored.
- The Python backend migration starts in `apps/server`; `apps/api` remains available until feature parity is reached.
- The Python backend now targets PostgreSQL by default via `ANIMA_DATABASE_URL`.
- Auth is local-owner bootstrap (no mandatory email identity flow).
- User backup/sync uses encrypted vault export/import with a user passphrase (argon2id + AES-256-GCM).
- Keep prompts in `apps/api/prompts/*.md` and load via `renderPromptTemplate(...)`.
- Use `apps/api/src/lib/task-date.ts` as the shared due-date logic source.

## Docs

- [`docs/whitepaper.md`](docs/whitepaper.md)
- [`docs/roadmap.md`](docs/roadmap.md)
- [`docs/build-release.md`](docs/build-release.md)
