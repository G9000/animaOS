---
title: API Routes
description: All REST endpoints grouped by router, dependency injection, and request/response schemas
category: architecture
---

# API Routes

[Back to Index](README.md)

## Route Map

### Auth (`/api/auth`) — `routes/auth.py`

| Method | Path | Purpose | Key Dependencies |
|--------|------|---------|-----------------|
| `POST` | `/register` | Create new user account | `user_store`, `sessions` |
| `POST` | `/login` | Authenticate and get unlock token | `user_store`, `sessions` |
| `POST` | `/logout` | Revoke unlock session | `sessions` |
| `GET` | `/me` | Get current user info | `require_unlocked_session` |
| `POST` | `/change-password` | Change user password | `auth` service |
| `POST` | `/create-ai/chat` | AI creation ceremony turn | `creation_agent` |

### Chat (`/api/chat`) — `routes/chat.py`

| Method | Path | Purpose | Key Dependencies |
|--------|------|---------|-----------------|
| `POST` | `` | Send message (stream or blocking) | `service.py` |
| `GET` | `/history` | Get conversation history | `persistence.py` |
| `DELETE` | `/history` | Clear conversation history | `persistence.py` |
| `POST` | `/reset` | Reset agent thread | `service.py` |
| `GET` | `/brief` | Get daily brief | `proactive.py` |
| `GET` | `/greeting` | Get LLM-generated greeting | `proactive.py` |
| `GET` | `/nudges` | Get proactive nudges | `proactive.py` |
| `GET` | `/home` | Get home dashboard data | `proactive.py` |
| `POST` | `/consolidate` | Trigger memory consolidation | `consolidation.py` |
| `POST` | `/sleep` | Trigger sleep tasks | `sleep_tasks.py` |
| `POST` | `/reflect` | Trigger reflection | `reflection.py` |
| `POST` | `/dry-run` | Dry-run agent turn (no persistence) | `service.py` |
| `POST` | `/runs/{id}/cancel` | Cancel an active run | `service.py` |
| `POST` | `/runs/{id}/approval` | Approve/deny a pending tool call | `service.py` |

### Memory (`/api/memory`) — `routes/memory.py`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/{user_id}` | Memory overview (counts, stats) |
| `GET` | `/{user_id}/items` | List memory items with filters |
| `POST` | `/{user_id}/items` | Create a memory item |
| `PUT` | `/{user_id}/items/{id}` | Update a memory item |
| `DELETE` | `/{user_id}/items/{id}` | Delete a memory item |
| `GET` | `/{user_id}/search` | Hybrid search (semantic + keyword) |
| `GET` | `/{user_id}/episodes` | List episodic memories |

### Consciousness (`/api/consciousness`) — `routes/consciousness.py`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/{user_id}/self-model` | Get full self-model (all 5 sections) |
| `GET` | `/{user_id}/self-model/{section}` | Get one self-model section |
| `PUT` | `/{user_id}/self-model/{section}` | Update one self-model section |
| `GET` | `/{user_id}/emotions` | Get recent emotional signals |
| `GET` | `/{user_id}/intentions` | Get active intentions |

### Soul (`/api/soul`) — `routes/soul.py`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/{user_id}` | Get user directive (soul text) |
| `PUT` | `/{user_id}` | Update user directive |

### Tasks (`/api/tasks`) — `routes/tasks.py`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `` | List tasks |
| `POST` | `` | Create a task |
| `PUT` | `/{id}` | Update a task |
| `DELETE` | `/{id}` | Delete a task |

### Users (`/api/users`) — `routes/users.py`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/{user_id}` | Get user profile |
| `PUT` | `/{user_id}` | Update user profile |
| `DELETE` | `/{user_id}` | Delete user account |

### Vault (`/api/vault`) — `routes/vault.py`

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/export` | Export encrypted vault backup |
| `POST` | `/import` | Import vault from backup |

### Config (`/api/config`) — `routes/config.py`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/providers` | List available LLM providers |
| `GET` | `/{user_id}` | Get user's LLM config |
| `PUT` | `/{user_id}` | Update user's LLM config |

### Core (`/api/core`) — `routes/core.py`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/status` | System status (provisioned, encryption mode) |

### DB (`/api/db`) — `routes/db.py`

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/tables` | List all tables |
| `GET` | `/tables/{name}` | Get table schema |
| `POST` | `/query` | Execute raw SQL query |
| `DELETE` | `/tables/{name}/rows` | Delete rows by IDs |
| `PUT` | `/tables/{name}/rows` | Update rows |
| `POST` | `/verify-password` | Verify encryption password |

## Dependency Injection (`api/deps/`)

| File | Function | Purpose |
|------|----------|---------|
| `unlock.py` | `read_unlock_token(request)` | Extracts `x-anima-unlock` header |
| `unlock.py` | `require_unlocked_session(request)` | Returns `UnlockSession` or 401 |
| `unlock.py` | `require_unlocked_user(request, user_id)` | Session + user ID match or 403 |
| `db_mode.py` | `require_sqlite_mode()` | Ensures SQLite mode is active |
