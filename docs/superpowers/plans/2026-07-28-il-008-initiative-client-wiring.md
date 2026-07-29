# IL-008 Initiative Client Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a fired IL3 push-initiative actually reach the user: typed api-client methods for the fetch/ack endpoints, a desktop poll/display/ack path, and presence-config UI for the four backend fields the client still omits (`initiativeEnabled`, `quietHoursStart`, `quietHoursEnd`, `dreamSharing`).

**Architecture:** We implement the **poll/display path** (option A in the ticket), not the `OSNotificationDelivery` Tauri adapter — the desktop shell has no notification plugin and `PendingInitiativeDelivery` (pollable rows + fetch/ack routes in `api/routes/presence.py`) is the only adapter that ships enabled. A framework-free poller module (dependency-injected, bun-testable) drives a small React hook; a global overlay in `Layout.tsx` surfaces the oldest pending initiative and acks it. The server side is complete and already covered by route tests (`apps/server/tests/test_inner_life_initiative.py:1494-1534`) — this plan touches **no server code**.

**Tech Stack:** TypeScript, React 18, react-router-dom, Tailwind classes (existing app idiom), `bun:test` with injected `fetchImpl` (existing `packages/api-client/tests/client.test.ts` pattern).

## Global Constraints

- Server contract is fixed and camelCase; do not change it. `PendingInitiativeResponse`: `{ id: number, drive: string, text: string, createdAt: string, delivered: boolean, acknowledged: boolean }`; list response: `{ userId, initiatives: [...] }` (see `apps/server/src/anima_server/schemas/presence.py`).
- Presence-config field values, verbatim from the server schema: `initiativeEnabled: bool`, `quietHoursStart`/`quietHoursEnd: int | None` in `0..23`, `dreamSharing` matching `^(off|on_ask|ambient)$` (default `on_ask`).
- Quiet hours are inactive unless **both** start and end are set and differ (`initiative.py::_in_quiet_hours`) — the UI must hint this.
- The GET poll marks rows `delivered` server-side; **acknowledge is a user action** (dismiss/reply), never automatic on render.
- Push initiative is off by default; nothing may surface unless the user opts in (`initiativeEnabled`).
- Poll errors are swallowed silently (locked session / server down must not spam the UI); default poll interval 60 000 ms plus a poll on window focus.
- Match the existing desktop visual idiom: `font-mono` uppercase micro-labels with wide tracking, `border-border` panels — see `apps/desktop/src/pages/Presence.tsx` for reference.
- Work on branch `feature/il-008-initiative-client-wiring`; commit per task; never commit to `main`.

---

### Task 1: api-client — presence-config fields + initiative endpoints

**Files:**
- Modify: `packages/api-client/src/types.ts:617-630` (PresenceConfig block)
- Modify: `packages/api-client/src/client.ts:914-922` (presence section)
- Test: `packages/api-client/tests/client.test.ts` (append)

**Interfaces:**
- Consumes: existing `createApiClient` / `request` helper (already supports `method`, optional `body`).
- Produces (later tasks rely on these exact names):
  - `type DreamSharing = "off" | "on_ask" | "ambient"`
  - `interface PendingInitiative { id: number; drive: string; text: string; createdAt: string; delivered: boolean; acknowledged: boolean }`
  - `interface PendingInitiativesResponse { userId: number; initiatives: PendingInitiative[] }`
  - `api.presence.initiatives(userId: number): Promise<PendingInitiativesResponse>`
  - `api.presence.ackInitiative(userId: number, initiativeId: number): Promise<PendingInitiative>`
  - `PresenceConfig` gains `initiativeEnabled: boolean; quietHoursStart: number | null; quietHoursEnd: number | null; dreamSharing: DreamSharing` (and `PresenceConfigUpdate` picks them up automatically via its existing `Partial<Omit<PresenceConfig, "userId">>`).
  - All are re-exported via the existing `export * from "./types"` in `packages/api-client/src/index.ts` — no index change needed.

- [ ] **Step 1: Write the failing tests**

Append to `packages/api-client/tests/client.test.ts`, inside a new `describe("presence initiatives", ...)` block, following the file's existing `fetchImpl` style:

```ts
describe("presence initiatives", () => {
  test("fetches pending initiatives and acknowledges by id", async () => {
    const calls: { url: string; method: string }[] = [];
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      getUnlockToken: () => "unlock-token",
      fetchImpl: async (input, init) => {
        calls.push({ url: String(input), method: init?.method ?? "GET" });
        if (String(input).endsWith("/ack")) {
          return new Response(
            JSON.stringify({
              id: 7,
              drive: "closeness",
              text: "I kept thinking about the harbor photos.",
              createdAt: "2026-07-28T02:00:00+00:00",
              delivered: true,
              acknowledged: true,
            }),
          );
        }
        return new Response(
          JSON.stringify({
            userId: 42,
            initiatives: [
              {
                id: 7,
                drive: "closeness",
                text: "I kept thinking about the harbor photos.",
                createdAt: "2026-07-28T02:00:00+00:00",
                delivered: true,
                acknowledged: false,
              },
            ],
          }),
        );
      },
    });

    const list = await api.presence.initiatives(42);
    expect(list.userId).toBe(42);
    expect(list.initiatives).toHaveLength(1);
    expect(list.initiatives[0].id).toBe(7);
    expect(calls[0]).toEqual({
      url: "https://api.test/api/presence/42/initiatives",
      method: "GET",
    });

    const acked = await api.presence.ackInitiative(42, 7);
    expect(acked.acknowledged).toBe(true);
    expect(calls[1]).toEqual({
      url: "https://api.test/api/presence/42/initiatives/7/ack",
      method: "POST",
    });
  });

  test("sends the four inner-life presence-config fields on update", async () => {
    let requestBody: unknown = null;
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      getUnlockToken: () => "unlock-token",
      fetchImpl: async (_input, init) => {
        requestBody = JSON.parse(String(init?.body));
        return new Response(
          JSON.stringify({
            userId: 42,
            enabled: true,
            mainChatEnabled: true,
            homeGreetingContextEnabled: true,
            taskNudgesEnabled: true,
            memoryNudgesEnabled: true,
            checkInNudgesEnabled: true,
            customInstruction: null,
            initiativeEnabled: true,
            quietHoursStart: 22,
            quietHoursEnd: 7,
            dreamSharing: "ambient",
          }),
        );
      },
    });

    const config = await api.presence.update(42, {
      initiativeEnabled: true,
      quietHoursStart: 22,
      quietHoursEnd: 7,
      dreamSharing: "ambient",
    });

    expect(requestBody).toEqual({
      initiativeEnabled: true,
      quietHoursStart: 22,
      quietHoursEnd: 7,
      dreamSharing: "ambient",
    });
    expect(config.initiativeEnabled).toBe(true);
    expect(config.dreamSharing).toBe("ambient");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd packages/api-client && bun test`
Expected: FAIL — `api.presence.initiatives is not a function` (and a TS error on the update-payload fields).

- [ ] **Step 3: Add the types**

In `packages/api-client/src/types.ts`, extend the `PresenceConfig` block (currently lines 617-630) to:

```ts
export type DreamSharing = "off" | "on_ask" | "ambient";

export interface PresenceConfig {
  userId: number;
  enabled: boolean;
  mainChatEnabled: boolean;
  homeGreetingContextEnabled: boolean;
  taskNudgesEnabled: boolean;
  memoryNudgesEnabled: boolean;
  checkInNudgesEnabled: boolean;
  customInstruction?: string | null;
  initiativeEnabled: boolean;
  quietHoursStart: number | null;
  quietHoursEnd: number | null;
  dreamSharing: DreamSharing;
}

export type PresenceConfigUpdate = Partial<
  Omit<PresenceConfig, "userId">
>;

export interface PendingInitiative {
  id: number;
  drive: string;
  text: string;
  createdAt: string;
  delivered: boolean;
  acknowledged: boolean;
}

export interface PendingInitiativesResponse {
  userId: number;
  initiatives: PendingInitiative[];
}
```

- [ ] **Step 4: Add the client methods**

In `packages/api-client/src/client.ts`, extend the `presence` section (currently lines 914-922) to:

```ts
presence: {
  get: (userId: number) =>
    request<PresenceConfig>(`/presence/${userId}`),
  update: (userId: number, data: PresenceConfigUpdate) =>
    request<PresenceConfig>(`/presence/${userId}`, {
      method: "PUT",
      body: data,
    }),
  initiatives: (userId: number) =>
    request<PendingInitiativesResponse>(`/presence/${userId}/initiatives`),
  ackInitiative: (userId: number, initiativeId: number) =>
    request<PendingInitiative>(
      `/presence/${userId}/initiatives/${initiativeId}/ack`,
      { method: "POST" },
    ),
},
```

Add `PendingInitiative` and `PendingInitiativesResponse` to the type-import list at the top of `client.ts` (near the existing `PresenceConfig, PresenceConfigUpdate` imports at lines 62-63). If `request`'s options type requires `body` on non-GET calls, pass `{ method: "POST" }` only — check the helper's signature first; it already handles PUT-with-body, and a bodyless POST must not send `"undefined"`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd packages/api-client && bun test`
Expected: PASS (all pre-existing tests in the file must stay green too).

- [ ] **Step 6: Commit**

```bash
git add packages/api-client
git commit -m "IL-008: api-client types + methods for pending initiatives and inner-life presence config"
```

---

### Task 2: Desktop initiative poller (framework-free, bun-tested)

**Files:**
- Create: `apps/desktop/src/lib/initiativePoller.ts`
- Test: `apps/desktop/tests/initiativePoller.test.ts` (new directory; run with `cd apps/desktop && bun test`, same pattern as `apps/anima-mod`)

**Interfaces:**
- Consumes: `PendingInitiative` from `@anima/api-client` (Task 1).
- Produces (Task 3 relies on these exact names):
  - `interface InitiativePollerDeps { fetchInitiatives: () => Promise<PendingInitiative[]>; ackInitiative: (id: number) => Promise<unknown>; onChange: (pending: PendingInitiative[]) => void; intervalMs?: number; setIntervalFn?: typeof setInterval; clearIntervalFn?: typeof clearInterval }`
  - `interface InitiativePoller { start(): void; stop(): void; pollNow(): Promise<void>; ack(id: number): Promise<void> }`
  - `function createInitiativePoller(deps: InitiativePollerDeps): InitiativePoller`

- [ ] **Step 1: Write the failing tests**

Create `apps/desktop/tests/initiativePoller.test.ts`:

```ts
import { describe, expect, test } from "bun:test";
import type { PendingInitiative } from "@anima/api-client";

import { createInitiativePoller } from "../src/lib/initiativePoller";

function row(id: number, overrides: Partial<PendingInitiative> = {}): PendingInitiative {
  return {
    id,
    drive: "closeness",
    text: `initiative ${id}`,
    createdAt: "2026-07-28T02:00:00+00:00",
    delivered: true,
    acknowledged: false,
    ...overrides,
  };
}

describe("createInitiativePoller", () => {
  test("pollNow replaces the pending list from the server and reports it", async () => {
    const seen: PendingInitiative[][] = [];
    const poller = createInitiativePoller({
      fetchInitiatives: async () => [row(1), row(2)],
      ackInitiative: async () => ({}),
      onChange: (pending) => seen.push(pending),
    });
    await poller.pollNow();
    expect(seen).toHaveLength(1);
    expect(seen[0].map((r) => r.id)).toEqual([1, 2]);
  });

  test("a failed poll is swallowed and does not call onChange", async () => {
    const seen: PendingInitiative[][] = [];
    const poller = createInitiativePoller({
      fetchInitiatives: async () => {
        throw new Error("locked");
      },
      ackInitiative: async () => ({}),
      onChange: (pending) => seen.push(pending),
    });
    await poller.pollNow();
    expect(seen).toHaveLength(0);
  });

  test("overlapping polls do not double-fetch", async () => {
    let fetches = 0;
    let release: (() => void) | null = null;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const poller = createInitiativePoller({
      fetchInitiatives: async () => {
        fetches += 1;
        await gate;
        return [row(1)];
      },
      ackInitiative: async () => ({}),
      onChange: () => {},
    });
    const first = poller.pollNow();
    const second = poller.pollNow(); // must no-op while the first is in flight
    release?.();
    await Promise.all([first, second]);
    expect(fetches).toBe(1);
  });

  test("ack calls the API, removes the row locally, and reports the remainder", async () => {
    const ackedIds: number[] = [];
    const seen: PendingInitiative[][] = [];
    const poller = createInitiativePoller({
      fetchInitiatives: async () => [row(1), row(2)],
      ackInitiative: async (id) => {
        ackedIds.push(id);
        return {};
      },
      onChange: (pending) => seen.push(pending),
    });
    await poller.pollNow();
    await poller.ack(1);
    expect(ackedIds).toEqual([1]);
    expect(seen.at(-1)?.map((r) => r.id)).toEqual([2]);
  });

  test("a failed ack still removes the row locally (next poll re-serves it if unacked)", async () => {
    const seen: PendingInitiative[][] = [];
    const poller = createInitiativePoller({
      fetchInitiatives: async () => [row(1)],
      ackInitiative: async () => {
        throw new Error("offline");
      },
      onChange: (pending) => seen.push(pending),
    });
    await poller.pollNow();
    await poller.ack(1);
    expect(seen.at(-1)).toEqual([]);
  });

  test("start polls immediately, schedules the interval, and stop clears it", async () => {
    let scheduled: (() => void) | null = null;
    let intervalMsSeen = 0;
    let cleared = false;
    let fetches = 0;
    const poller = createInitiativePoller({
      fetchInitiatives: async () => {
        fetches += 1;
        return [];
      },
      ackInitiative: async () => ({}),
      onChange: () => {},
      intervalMs: 60_000,
      setIntervalFn: ((fn: () => void, ms: number) => {
        scheduled = fn;
        intervalMsSeen = ms;
        return 123 as unknown as ReturnType<typeof setInterval>;
      }) as typeof setInterval,
      clearIntervalFn: (() => {
        cleared = true;
      }) as typeof clearInterval,
    });
    poller.start();
    poller.start(); // idempotent — must not double-schedule
    await Promise.resolve();
    expect(fetches).toBe(1);
    expect(intervalMsSeen).toBe(60_000);
    scheduled?.();
    // let the scheduled async poll settle
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetches).toBe(2);
    poller.stop();
    expect(cleared).toBe(true);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/desktop && bun test`
Expected: FAIL — cannot resolve `../src/lib/initiativePoller`.

- [ ] **Step 3: Write the implementation**

Create `apps/desktop/src/lib/initiativePoller.ts`:

```ts
import type { PendingInitiative } from "@anima/api-client";

export interface InitiativePollerDeps {
  fetchInitiatives: () => Promise<PendingInitiative[]>;
  ackInitiative: (id: number) => Promise<unknown>;
  onChange: (pending: PendingInitiative[]) => void;
  /** Default 60_000. */
  intervalMs?: number;
  /** Injectable for tests. */
  setIntervalFn?: typeof setInterval;
  clearIntervalFn?: typeof clearInterval;
}

export interface InitiativePoller {
  start(): void;
  stop(): void;
  pollNow(): Promise<void>;
  ack(id: number): Promise<void>;
}

/**
 * Polls the pending-initiative endpoint (the server marks fetched rows
 * `delivered`) and holds the client-side pending list. Acknowledge is a
 * user action: `ack()` removes the row locally even if the API call fails —
 * the server is the source of truth, so an unacked row simply comes back on
 * the next poll.
 */
export function createInitiativePoller(
  deps: InitiativePollerDeps,
): InitiativePoller {
  const intervalMs = deps.intervalMs ?? 60_000;
  const setIntervalFn = deps.setIntervalFn ?? setInterval;
  const clearIntervalFn = deps.clearIntervalFn ?? clearInterval;
  let timer: ReturnType<typeof setInterval> | null = null;
  let inFlight = false;
  let pending: PendingInitiative[] = [];

  const pollNow = async (): Promise<void> => {
    if (inFlight) return;
    inFlight = true;
    try {
      pending = await deps.fetchInitiatives();
      deps.onChange(pending);
    } catch {
      // Best-effort poll: a locked session or unreachable server must stay
      // silent; the next tick retries.
    } finally {
      inFlight = false;
    }
  };

  return {
    start() {
      if (timer !== null) return;
      timer = setIntervalFn(() => {
        void pollNow();
      }, intervalMs);
      void pollNow();
    },
    stop() {
      if (timer !== null) {
        clearIntervalFn(timer);
        timer = null;
      }
    },
    pollNow,
    async ack(id: number) {
      try {
        await deps.ackInitiative(id);
      } catch {
        // The server still holds the row; the next poll re-serves it.
      }
      pending = pending.filter((rowItem) => rowItem.id !== id);
      deps.onChange(pending);
    },
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/desktop && bun test`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/lib/initiativePoller.ts apps/desktop/tests/initiativePoller.test.ts
git commit -m "IL-008: framework-free pending-initiative poller for the desktop shell"
```

---

### Task 3: usePendingInitiatives hook + InitiativeOverlay mounted in Layout

**Files:**
- Create: `apps/desktop/src/hooks/usePendingInitiatives.ts`
- Create: `apps/desktop/src/components/InitiativeOverlay.tsx`
- Modify: `apps/desktop/src/components/Layout.tsx` (mount the overlay)

**Interfaces:**
- Consumes: `createInitiativePoller` (Task 2), `api.presence.initiatives` / `api.presence.ackInitiative` (Task 1), `useAuth` from `../context/AuthContext` (existing — `user?.id` as in `Presence.tsx:49`).
- Produces: `usePendingInitiatives(userId: number | null | undefined): { pending: PendingInitiative[]; ack: (id: number) => Promise<void> }`; default-exported `InitiativeOverlay` React component.

There is no React test runner in `apps/desktop` (the app has none today — do not add one for this ticket); the poller logic is already covered by Task 2, so this task's verification is the TypeScript compile plus a manual smoke check.

- [ ] **Step 1: Write the hook**

Create `apps/desktop/src/hooks/usePendingInitiatives.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import type { PendingInitiative } from "@anima/api-client";

import { api } from "../lib/api";
import {
  createInitiativePoller,
  type InitiativePoller,
} from "../lib/initiativePoller";

/**
 * Global poll for IL3 pending initiatives: 60s interval plus a poll on
 * window focus. Poll failures are silent (locked session, server down);
 * `ack` is the user's dismiss/reply action.
 */
export function usePendingInitiatives(userId: number | null | undefined): {
  pending: PendingInitiative[];
  ack: (id: number) => Promise<void>;
} {
  const [pending, setPending] = useState<PendingInitiative[]>([]);
  const pollerRef = useRef<InitiativePoller | null>(null);

  useEffect(() => {
    if (userId == null) return;
    const poller = createInitiativePoller({
      fetchInitiatives: async () =>
        (await api.presence.initiatives(userId)).initiatives,
      ackInitiative: (id) => api.presence.ackInitiative(userId, id),
      onChange: setPending,
    });
    pollerRef.current = poller;
    poller.start();
    const onFocus = () => {
      void poller.pollNow();
    };
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      poller.stop();
      pollerRef.current = null;
      setPending([]);
    };
  }, [userId]);

  const ack = useCallback(async (id: number) => {
    await pollerRef.current?.ack(id);
  }, []);

  return { pending, ack };
}
```

- [ ] **Step 2: Write the overlay component**

Create `apps/desktop/src/components/InitiativeOverlay.tsx`:

```tsx
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { usePendingInitiatives } from "../hooks/usePendingInitiatives";

/**
 * Surfaces the oldest pending IL3 initiative as a corner card. Rendered
 * only when something is pending — the server never creates rows unless
 * the user opted in (`initiativeEnabled`).
 */
export default function InitiativeOverlay() {
  const { user } = useAuth();
  const { pending, ack } = usePendingInitiatives(user?.id);
  const current = pending[0];
  if (!current) return null;

  return (
    <div className="pointer-events-auto fixed bottom-6 right-6 z-40 w-80 border border-primary/70 bg-card/95 p-4 backdrop-blur">
      <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground/60">
        {current.drive.replace(/_/g, " ")}
      </p>
      <p className="mt-2 text-sm leading-relaxed text-foreground">
        {current.text}
      </p>
      <div className="mt-4 flex items-center justify-between gap-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground/40">
          {pending.length > 1 ? `+${pending.length - 1} more` : ""}
        </span>
        <div className="flex gap-2">
          <Link
            to="/chat"
            onClick={() => void ack(current.id)}
            className="border border-primary bg-input px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-foreground transition-colors hover:bg-background"
          >
            Reply
          </Link>
          <button
            type="button"
            onClick={() => void ack(current.id)}
            className="border border-border px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground transition-colors hover:text-foreground"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Mount it in Layout**

Modify `apps/desktop/src/components/Layout.tsx` (whole file is 20 lines; add the import and one element):

```tsx
import type { ReactNode } from "react";
import { LayoutHUD } from "../features/hud";
import BackgroundLayer from "./BackgroundLayer";
import InitiativeOverlay from "./InitiativeOverlay";
import { LayoutActionsProvider } from "../context/LayoutActionsContext";

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <LayoutActionsProvider>
      <div className="relative h-screen text-foreground overflow-hidden">
        <BackgroundLayer />
        {/* Nav floats above everything */}
        <div className="absolute z-30 w-full pointer-events-none">
          <LayoutHUD />
        </div>
        {/* Content fills full height */}
        <main className="h-full overflow-hidden min-w-0">{children}</main>
        <InitiativeOverlay />
      </div>
    </LayoutActionsProvider>
  );
}
```

- [ ] **Step 4: Verify the TypeScript build**

Run: `cd apps/desktop && bunx tsc --noEmit`
Expected: zero errors. (Also re-run `bun test` — still green.)

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/hooks/usePendingInitiatives.ts apps/desktop/src/components/InitiativeOverlay.tsx apps/desktop/src/components/Layout.tsx
git commit -m "IL-008: surface pending initiatives via a global overlay (poll + focus refresh + ack)"
```

---

### Task 4: Presence page — initiative opt-in, quiet hours, dream sharing

**Files:**
- Modify: `apps/desktop/src/pages/Presence.tsx`

**Interfaces:**
- Consumes: the four new `PresenceConfig` fields (Task 1). No later task consumes this one.

- [ ] **Step 1: Extend DEFAULT_CONFIG and the save payload**

In `apps/desktop/src/pages/Presence.tsx`:

`DEFAULT_CONFIG` (lines 6-15) becomes:

```tsx
const DEFAULT_CONFIG: PresenceConfig = {
  userId: 0,
  enabled: true,
  mainChatEnabled: true,
  homeGreetingContextEnabled: true,
  taskNudgesEnabled: true,
  memoryNudgesEnabled: true,
  checkInNudgesEnabled: true,
  customInstruction: null,
  initiativeEnabled: false,
  quietHoursStart: null,
  quietHoursEnd: null,
  dreamSharing: "on_ask",
};
```

`handleSave`'s update payload (lines 107-115) gains the four fields:

```tsx
const next = await api.presence.update(user.id, {
  enabled: config.enabled,
  mainChatEnabled: config.mainChatEnabled,
  homeGreetingContextEnabled: config.homeGreetingContextEnabled,
  taskNudgesEnabled: config.taskNudgesEnabled,
  memoryNudgesEnabled: config.memoryNudgesEnabled,
  checkInNudgesEnabled: config.checkInNudgesEnabled,
  customInstruction: config.customInstruction || null,
  initiativeEnabled: config.initiativeEnabled,
  quietHoursStart: config.quietHoursStart,
  quietHoursEnd: config.quietHoursEnd,
  dreamSharing: config.dreamSharing,
});
```

- [ ] **Step 2: Add the sidebar status line and count**

The `activeCount` computation (lines 86-92) includes the new toggle, and the header chip (line 140) becomes `/6`:

```tsx
const activeCount = [
  config.mainChatEnabled,
  config.homeGreetingContextEnabled,
  config.taskNudgesEnabled,
  config.memoryNudgesEnabled,
  config.checkInNudgesEnabled,
  config.initiativeEnabled,
].filter(Boolean).length;
```

```tsx
{loading ? "Syncing" : `${activeCount}/6 Active`}
```

After the existing `StatusLine` rows (lines 176-186) add:

```tsx
<StatusLine
  label="Initiative"
  enabled={config.enabled && config.initiativeEnabled}
/>
```

- [ ] **Step 3: Add the Initiative control group**

Add a module-level constant next to `SIGNAL_OPTIONS`:

```tsx
const DREAM_SHARING_OPTIONS = [
  { value: "off", label: "Off" },
  { value: "on_ask", label: "On Ask" },
  { value: "ambient", label: "Ambient" },
] as const;
```

Insert a new `ControlGroup` between the "Signals" group and the "Default Direction" section (after line 215):

```tsx
<ControlGroup title="Initiative">
  <SwitchRow
    label="Unprompted Messages"
    detail="May reach out when a drive crosses its threshold"
    checked={config.initiativeEnabled}
    disabled={!config.enabled}
    onChange={(checked) => updateDraft({ initiativeEnabled: checked })}
  />
  <div className="flex items-center justify-between gap-4 px-1 py-4">
    <span className="min-w-0 space-y-1">
      <span className="block text-sm text-foreground">Quiet Hours</span>
      <span className="block text-xs text-muted-foreground">
        No messages inside this window — set both to enable
      </span>
    </span>
    <span className="flex items-center gap-2">
      <HourSelect
        value={config.quietHoursStart}
        disabled={!config.enabled || !config.initiativeEnabled}
        onChange={(value) => updateDraft({ quietHoursStart: value })}
      />
      <span className="font-mono text-[10px] text-muted-foreground/40">
        TO
      </span>
      <HourSelect
        value={config.quietHoursEnd}
        disabled={!config.enabled || !config.initiativeEnabled}
        onChange={(value) => updateDraft({ quietHoursEnd: value })}
      />
    </span>
  </div>
  <div className="flex items-center justify-between gap-4 px-1 py-4">
    <span className="min-w-0 space-y-1">
      <span className="block text-sm text-foreground">Dream Sharing</span>
      <span className="block text-xs text-muted-foreground">
        Whether night reflections may surface
      </span>
    </span>
    <span className="flex border border-border">
      {DREAM_SHARING_OPTIONS.map((option) => (
        <button
          key={option.value}
          type="button"
          disabled={!config.enabled}
          onClick={() => updateDraft({ dreamSharing: option.value })}
          className={`px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.16em] transition-colors disabled:opacity-45 ${
            config.dreamSharing === option.value
              ? "bg-primary/15 text-foreground"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {option.label}
        </button>
      ))}
    </span>
  </div>
</ControlGroup>
```

Add the `HourSelect` helper next to the other bottom-of-file helpers (`StatusLine`, `SwitchRow`):

```tsx
function HourSelect({
  value,
  disabled,
  onChange,
}: {
  value: number | null;
  disabled: boolean;
  onChange: (value: number | null) => void;
}) {
  return (
    <select
      value={value == null ? "" : String(value)}
      disabled={disabled}
      onChange={(event) => {
        const raw = event.currentTarget.value;
        onChange(raw === "" ? null : Number(raw));
      }}
      className="border border-border bg-input px-2 py-1.5 font-mono text-xs text-foreground outline-none transition-colors focus:border-primary disabled:opacity-45"
    >
      <option value="">—</option>
      {Array.from({ length: 24 }, (_, hour) => (
        <option key={hour} value={hour}>
          {String(hour).padStart(2, "0")}:00
        </option>
      ))}
    </select>
  );
}
```

- [ ] **Step 4: Verify the TypeScript build**

Run: `cd apps/desktop && bunx tsc --noEmit`
Expected: zero errors.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/pages/Presence.tsx
git commit -m "IL-008: presence page controls for initiative opt-in, quiet hours, dream sharing"
```

---

### Task 5: Full validation, ticket close-out, PR

**Files:**
- Modify: `tickets/inner-life-v1/IL-008-initiative-delivery-client-wiring.md`
- Modify: `tickets/inner-life-v1/IL-000-parent.md`

**Interfaces:**
- Consumes: everything above. Produces the PR.

- [ ] **Step 1: Run the full validation set**

```bash
cd packages/api-client && bun test
cd ../../apps/desktop && bun test && bunx tsc --noEmit
cd ../.. && bun run test   # server pytest — must stay at the green baseline (no server code was touched)
```

Expected: api-client and desktop suites pass; server suite unchanged from the green baseline.

- [ ] **Step 2: Update the tickets**

In `IL-008-initiative-delivery-client-wiring.md`: set `Status: done` (pending review → follow repo convention of marking done at merge if preferred — mirror what IL-007 did), set `Started`/`Completed`/`Updated` dates, fill the Activity Log with what was built (poller module + overlay + presence controls + api-client methods, and that the OSNotificationDelivery adapter path was NOT taken because no Tauri notification bridge exists), and fill Validation with the exact commands and results from Step 1 plus the changed paths.

In `IL-000-parent.md`: mark `IL-008` done in the child table with a Completed Ticket History entry; if all children are now done, set the parent `Status: done` and `Completed:` date.

- [ ] **Step 3: Commit and open the PR**

```bash
git add tickets/inner-life-v1
git commit -m "IL-008: close out ticket and parent tracker"
git push -u origin feature/il-008-initiative-client-wiring
gh pr create --title "IL-008: wire push-initiative into the client (poller, overlay, presence controls)" --body "..."
```

PR body must cover: poll/display path chosen over OSNotificationDelivery (no Tauri notification bridge exists), the 60s+focus poll marking rows delivered, ack semantics (user dismiss/reply; failed ack self-heals via next poll), the four presence-config fields now exposed end-to-end, and the validation command results.

---

## Self-Review

- **Spec coverage:** ticket deliverable 1 (api-client methods) → Task 1; deliverable 2 (desktop poll/display path, OR-adapter resolved to poll path) → Tasks 2-3; deliverable 3 (end-to-end coverage) → poller unit tests (Task 2) + api-client contract tests (Task 1) + existing server route tests exercising opt-in → fire → fetch → ack (`test_inner_life_initiative.py:1494-1534`); deliverable 4 (presence-config client exposure, all four fields typed + UI) → Tasks 1 and 4. Acceptance "user actually receives a fired initiative through the shipped client" → overlay in Layout on every authenticated route; "acknowledgement round-trips" → ack methods + tests.
- **Placeholder scan:** all code blocks are complete; the only intentionally deferred text is the PR body prose (Task 5 lists its required content).
- **Type consistency:** `PendingInitiative`/`PendingInitiativesResponse`/`DreamSharing` defined in Task 1 and consumed by name in Tasks 2-4; `createInitiativePoller`/`InitiativePoller` defined in Task 2, consumed in Task 3; `usePendingInitiatives` defined in Task 3, consumed by `InitiativeOverlay` in the same task.
