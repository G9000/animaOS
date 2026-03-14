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

The server defaults to a local SQLite database under `.anima/dev/anima.db`.

Example default:

```bash
ANIMA_DATABASE_URL=sqlite:///C:/path/to/animaOS/.anima/dev/anima.db
```

Override it in `.env` if you want Postgres instead:

```bash
ANIMA_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5433/anima
```
