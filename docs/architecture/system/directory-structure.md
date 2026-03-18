---
title: Directory Structure
description: Top-level folder layout and purpose of each directory in AnimaOS
category: architecture
---

# Directory Structure

[Back to Index](README.md)

## Top-Level Layout

```
animaOS/
  .anima/                    # The Core -- all AI state lives here (SQLite DBs, manifest)
  .claude/                   # Claude Code project memory
  apps/
    api/                     # Legacy Bun/TypeScript backend (being superseded)
    desktop/                 # Tauri + Vite frontend (TypeScript/React)
    server/                  # PRIMARY Python/FastAPI backend (all new work targets this)
      alembic/               # Database migration scripts
      alembic.ini            # Alembic configuration
      cli.py                 # CLI entry point
      src/anima_server/      # Main Python package
      tests/                 # Test suite (50 files, 602 tests)
  docs/                      # Research docs, thesis, whitepaper
  packages/                  # Shared packages (Nx monorepo)
  scripts/                   # Dev/build scripts
  pyproject.toml             # Python project config (workspace root)
  nx.json                    # Nx monorepo config
  package.json               # Node workspace config
```

## Python Package Map (`apps/server/src/anima_server/`)

### Entry Points

| File | Responsibility | Key Functions |
|------|---------------|---------------|
| `main.py` | FastAPI app factory, router mounting, middleware setup, shutdown hooks | `create_app()` (line 63), `SidecarNonceMiddleware` (line 36) |
| `config.py` | Pydantic Settings (env vars with `ANIMA_` prefix), all configuration | `Settings` class (line 9), 50 config fields |
| `cli.py` | CLI entry point for Alembic/DB management | |
| `cli/db.py` | DB management CLI commands | |

### Package Structure

```
anima_server/
  main.py                    # App factory
  config.py                  # Settings (Pydantic)
  api/
    routes/                  # 11 FastAPI routers
    deps/                    # Dependency injection (unlock, db_mode)
  services/
    agent/                   # Agent runtime, memory, consciousness (30+ files)
    crypto.py                # Argon2id + AES-GCM
    data_crypto.py           # Field-level encrypt/decrypt
    auth.py                  # Password hashing
    sessions.py              # Unlock session store
    vault.py                 # Encrypted export/import
    core.py                  # Manifest management
    storage.py               # File/blob storage
    creation_agent.py        # AI creation ceremony
  db/
    base.py                  # SQLAlchemy DeclarativeBase
    session.py               # Engine + session factory
    url.py                   # DB URL construction
    user_store.py            # User account management
  models/
    agent_runtime.py         # 14 tables (threads, messages, runs, memory, etc.)
    consciousness.py         # 3 tables (self_model, profile, emotions)
    user.py                  # User table
    user_key.py              # UserKey table (per-domain DEKs)
    task.py                  # Task table
    links.py                 # Telegram/Discord link tables
```
