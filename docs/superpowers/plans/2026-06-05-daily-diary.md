# Daily Diary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build encrypted user-authored daily diary storage and a usable Journal UI that accepts text plus file, recorded audio, audio uploads, and video attachments.

**Architecture:** Add Core SQLCipher diary tables, a small diary service for field/blob encryption, FastAPI routes under `/api/diary`, API client methods, and a redesigned desktop Journal page. Attachments are encrypted local blobs and are only decrypted for an unlocked owning user.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic Core migrations, AES-GCM helpers, Pydantic, React/Vite/Tailwind, Bun workspace API client.

---

### Task 1: Backend Tests

**Files:**
- Create: `apps/server/tests/test_diary_api.py`

- [ ] Write failing API tests for create/list, attachment upload, encrypted-at-rest blob check, and download auth.
- [ ] Run `uv run pytest apps/server/tests/test_diary_api.py -q` and confirm failures are because `/api/diary` does not exist yet.

### Task 2: Core Models And Migration

**Files:**
- Modify: `apps/server/src/anima_server/models/agent_runtime.py`
- Modify: `apps/server/src/anima_server/models/__init__.py`
- Create: `apps/server/alembic_core/versions/20260605_0001_create_diary_tables.py`

- [ ] Add `DiaryEntry` and `DiaryAttachment` models.
- [ ] Register models in `models/__init__.py`.
- [ ] Add a symmetric Alembic Core migration with explicit indexes and cascade FKs.

### Task 3: Diary Service And Schemas

**Files:**
- Create: `apps/server/src/anima_server/schemas/diary.py`
- Create: `apps/server/src/anima_server/services/diary.py`

- [ ] Add Pydantic request/response models.
- [ ] Add service helpers to encrypt/decrypt fields, encrypt/decrypt blobs, store attachments, and delete blob files.
- [ ] Keep filename/caption encrypted; keep MIME, media kind, size, checksum, storage path operational.

### Task 4: FastAPI Routes

**Files:**
- Create: `apps/server/src/anima_server/api/routes/diary.py`
- Modify: `apps/server/src/anima_server/main.py`

- [ ] Add list/create/upload/download/delete endpoints.
- [ ] Use `require_unlocked_session` and owner checks.
- [ ] Map validation errors to HTTP 400/404/403.
- [ ] Register the router.

### Task 5: API Client And UI

**Files:**
- Modify: `packages/api-client/src/types.ts`
- Modify: `packages/api-client/src/client.ts`
- Modify: `apps/desktop/src/pages/Journal.tsx`
- Optionally modify navigation labels only if needed.

- [ ] Add diary TypeScript interfaces and client methods.
- [ ] Replace Journal with a diary composer and timeline.
- [ ] Add browser recording and opportunistic speech-to-text in the composer.
- [ ] Preserve generated episodes as a secondary "Anima memories" section.
- [ ] Keep UI controls compact and operational, with file input accepting image/audio/video/generic files.

### Task 6: Verification

- [ ] Run `uv run pytest apps/server/tests/test_diary_api.py -q`.
- [ ] Run `bun run build`.
- [ ] Run `bun run lint` if build passes or if TypeScript errors need deeper checking.
