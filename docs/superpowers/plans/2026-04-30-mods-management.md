# Mods Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make mods management reliable in the backend and more useful in the desktop UI.

**Architecture:** Keep the existing anima-mod management API as the control plane. Normalize backend state/events at service boundaries, extend the API with recent events, and let desktop consume those contracts through `mod-client.ts`.

**Tech Stack:** Bun, Elysia, Drizzle SQLite, React, TypeScript, Vite.

---

### Task 1: Backend Management Contracts

**Files:**
- Modify: `apps/anima-mod/src/db/index.ts`
- Modify: `apps/anima-mod/src/management/state-service.ts`
- Modify: `apps/anima-mod/src/management/event-service.ts`
- Modify: `apps/anima-mod/src/management/ws.ts`
- Test: `apps/anima-mod/tests/management/state-service.test.ts`

- [ ] Write tests for normalized `ModState` defaults and typed event logging.
- [ ] Run `bun test tests/management/state-service.test.ts` and confirm failures.
- [ ] Type `getDb()` as `BunSQLiteDatabase<typeof schema>`.
- [ ] Normalize nullable state rows to stable defaults.
- [ ] Restrict event types to the schema union and parse event details.
- [ ] Loosen `createWsRouter()` return typing to avoid Elysia route generic mismatch.
- [ ] Rerun focused tests.

### Task 2: Management API Lifecycle and Events

**Files:**
- Modify: `apps/anima-mod/src/management/router.ts`
- Test: `apps/anima-mod/tests/management/router.test.ts`

- [ ] Write tests for lifecycle endpoints returning refreshed state.
- [ ] Write tests for `GET /api/mods/:id/events`.
- [ ] Write tests that uninstall built-in mods reports an error.
- [ ] Run `bun test tests/management/router.test.ts` and confirm failures.
- [ ] Add event hydration to mod detail.
- [ ] Add `/api/mods/:id/events`.
- [ ] Return normalized state payloads from enable/disable/restart/config update.
- [ ] Rerun focused tests.

### Task 3: Desktop Mod Client

**Files:**
- Modify: `apps/desktop/src/lib/mod-client.ts`
- Test: `apps/desktop/tests/mod-client.test.ts`

- [ ] Write tests for `getModEvents()` and `uninstallMod()`.
- [ ] Run the desktop test command or focused Bun test and confirm failures.
- [ ] Add `ModEvent` type and client methods.
- [ ] Rerun focused client tests.

### Task 4: Desktop Management UX

**Files:**
- Modify: `apps/desktop/src/components/mods/ModCard.tsx`
- Modify: `apps/desktop/src/components/mods/ConfigForm.tsx`
- Modify: `apps/desktop/src/pages/Mods.tsx`
- Modify: `apps/desktop/src/pages/ModDetail.tsx`
- Modify: `apps/desktop/src/components/mods/types.ts`

- [ ] Extend UI types for events and lifecycle details.
- [ ] Add search, status filters, status counts, refresh, and action feedback on `/mods`.
- [ ] Add event history, uninstall, config save errors, and action feedback on `/mods/:id`.
- [ ] Keep styling consistent with existing compact operational UI.

### Task 5: Verification

**Commands:**
- `bun run test:anima-mod`
- `bun run build:anima-mod`
- `bun test apps/desktop/tests/mod-client.test.ts`
- `bun run build:desktop`

- [ ] Run all verification commands.
- [ ] Report any remaining failures with exact files and causes.

