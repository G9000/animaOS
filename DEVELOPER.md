# anima-os-lite

Mixed monorepo for ANIMA:

- `apps/server`: Python + FastAPI backend managed with `uv`
- `apps/desktop`: Tauri + React desktop client orchestrated via `nx`
- `apps/anima-mod`: Elysia + Bun module system for external integrations (Telegram, Discord, etc.)
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
bun run db:server:upgrade
bun run dev
```

The server defaults to local SQLite at `.anima/dev/anima.db`. If you want to run
against Postgres instead, start it with `bun run db:server:up`, set
`ANIMA_DATABASE_URL`, then run `bun run db:server:upgrade`.

Run app-specific dev tasks:

```bash
bun run dev:server
bun run dev:desktop
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

## Repository Notes

- `memory/` is intentionally local-only and fully git-ignored.
- The Python backend defaults to SQLite at `.anima/dev/anima.db`. Set
  `ANIMA_DATABASE_URL` to use Postgres instead.
- Auth is local-owner bootstrap (no mandatory email identity flow).
- User backup/sync uses encrypted vault export/import with a user passphrase (argon2id + AES-256-GCM).

## Animus CLI (`apps/animus`)

A terminal coding agent that connects to the AnimaOS server via WebSocket. The server runs the agent loop (LLM calls, memory, tool rules) and delegates action tools (bash, file ops) to the CLI for local execution.

### Running

```bash
# Start the server first
bun run dev:server

# Launch the CLI (interactive TUI)
cd apps/animus && bun run dev

# Or with a custom server URL
cd apps/animus && bun run src/index.ts --server ws://remote-host:3031
```

On first run, you'll be prompted for credentials. Config is saved to `~/.animus/config.json`.

### Testing

```bash
bun run test:animus
```

### Architecture

- **Entry point**: `src/index.ts` — arg parsing, login, TUI launch
- **Connection**: `src/client/connection.ts` — WebSocket manager with reconnection
- **Auth**: `src/client/auth.ts` — config read/write, HTTP login
- **Protocol**: `src/client/protocol.ts` — all WS message type definitions
- **Tools**: `src/tools/` — 8 action tools (bash, read, write, edit, grep, glob, list_dir, ask_user)
- **Permissions**: `src/tools/permissions.ts` — CLI-side safety checks for dangerous commands
- **TUI**: `src/ui/` — ink/React components (Header, Chat, Input, ToolCall, Approval, Spinner)

## Docs

- [`docs/whitepaper.md`](docs/whitepaper.md)
- [`docs/roadmap.md`](docs/roadmap.md)
- [`docs/build-release.md`](docs/build-release.md)
