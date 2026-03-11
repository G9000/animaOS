# anima-server

FastAPI backend scaffold for the Python migration.

## Commands

```bash
uv sync --project apps/server
docker compose up -d postgres
uv run --project apps/server uvicorn anima_server.main:app --app-dir apps/server/src --reload --host 127.0.0.1 --port 3031
uv run --project apps/server alembic -c apps/server/alembic.ini heads
uv run --project apps/server alembic -c apps/server/alembic.ini current
uv run --project apps/server alembic -c apps/server/alembic.ini upgrade head
uv run --project apps/server pytest
uv run --project apps/server ruff check src tests
```

## Database

The server uses PostgreSQL by default:

```bash
ANIMA_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5433/anima
```

Override it in `.env` if needed.
