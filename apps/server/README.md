# anima-server

FastAPI backend used for the current local-development runtime.

Note: the packaged Tauri desktop app still bundles the legacy `apps/api`
sidecar. This Python server is the active backend for local development and the
main server-side implementation in this repo.

## Commands

```bash
uv sync --project apps/server
uv run --project apps/server uvicorn anima_server.main:app --app-dir apps/server/src --reload --host 127.0.0.1 --port 3031
bun run db:server:revision -- "create users table"
uv run --project apps/server alembic -c apps/server/alembic.ini heads
uv run --project apps/server alembic -c apps/server/alembic.ini current
uv run --project apps/server alembic -c apps/server/alembic.ini upgrade head
uv run --project apps/server pytest
uv run --project apps/server ruff check src tests
```

If you want to use Postgres instead of the default SQLite database, start it first:

```bash
docker compose up -d postgres
```

## Database

The server uses per-user SQLite databases under `.anima/dev/users/{id}/anima.db`,
encrypted with SQLCipher when a passphrase is configured.

### Migrations

Schema migrations run automatically on startup via Alembic. When
`ensure_user_database()` is called for a user, it runs
`alembic upgrade head` programmatically against that user's database engine.

- **Fresh databases**: the full migration chain creates all tables from scratch.
- **Legacy databases** (created before Alembic was wired in): automatically
  stamped at the current head revision.
- **Existing tracked databases**: only pending migrations are applied.

The CLI commands below are still available for manual inspection:

```bash
uv run --project apps/server alembic -c apps/server/alembic.ini heads
uv run --project apps/server alembic -c apps/server/alembic.ini current
```

### Adding a new migration

```bash
bun run db:server:revision -- "describe the change"
```

Edit the generated file in `alembic/versions/`, using `batch_alter_table`
for any operations that modify existing tables (SQLite requirement).
