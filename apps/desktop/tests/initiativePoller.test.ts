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

  test("an ack during an in-flight poll wins over the stale poll result", async () => {
    let release: (() => void) | null = null;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const seen: PendingInitiative[][] = [];
    let pollCount = 0;
    const poller = createInitiativePoller({
      fetchInitiatives: async () => {
        pollCount += 1;
        if (pollCount === 1) {
          // First poll: populate the list
          return [row(1), row(2)];
        }
        // Second poll: gated, will return the same data
        await gate;
        return [row(1), row(2)];
      },
      ackInitiative: async () => ({}),
      onChange: (pending) => seen.push(pending),
    });
    // First poll populates state
    await poller.pollNow();
    // Start second poll which is gated
    const pollPromise = poller.pollNow();
    // poll is now in flight, ack the row while it's gated
    await poller.ack(1);
    // release the gate so the poll completes
    release?.();
    await pollPromise;
    // the final state should not have row 1 (acked row won)
    expect(seen.at(-1)?.map((r) => r.id)).toEqual([2]);
  });
});
