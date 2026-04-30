import { afterEach, describe, expect, test } from "bun:test";

import { getModClient, resetModClient } from "../src/lib/mod-client";

const originalFetch = globalThis.fetch;

describe("mod client", () => {
  afterEach(() => {
    globalThis.fetch = originalFetch;
    resetModClient();
  });

  test("surfaces non-JSON error bodies instead of throwing JSON parse errors", async () => {
    globalThis.fetch = (async () =>
      new Response("upstream failed", {
        status: 502,
        headers: { "Content-Type": "text/plain" },
      })) as typeof fetch;

    await expect(getModClient().listMods()).rejects.toThrow("upstream failed");
  });

  test("fetches mod events", async () => {
    globalThis.fetch = (async (input) => {
      expect(String(input)).toBe("http://localhost:3034/api/mods/google/events");
      return Response.json([
        {
          id: 1,
          modId: "google",
          eventType: "started",
          detail: { source: "test" },
          createdAt: "2026-04-30T00:00:00.000Z",
        },
      ]);
    }) as typeof fetch;

    const events = await getModClient().getModEvents("google");

    expect(events).toHaveLength(1);
    expect(events[0].detail).toEqual({ source: "test" });
  });

  test("uninstalls mods through the management API", async () => {
    globalThis.fetch = (async (input, init) => {
      expect(String(input)).toBe("http://localhost:3034/api/mods/google/uninstall");
      expect(init?.method).toBe("POST");
      return Response.json({ status: "uninstalled" });
    }) as typeof fetch;

    const result = await getModClient().uninstallMod("google");

    expect(result.status).toBe("uninstalled");
  });
});
