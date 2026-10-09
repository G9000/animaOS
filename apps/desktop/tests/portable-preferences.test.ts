import { describe, expect, test } from "bun:test";
import { api } from "../src/lib/api";
import { clearPortablePreferences, getPortablePreference, hydratePortablePreferences, setPortablePreference } from "../src/lib/portablePreferences";

describe("current portable preferences", () => {
  test("hydrates only encrypted Core values without browser migration writes", async () => {
    let writes = 0;
    await hydratePortablePreferences(0, {
      get: async (userId) => ({ userId, values: { theme: "dark", clockFormat: "12h" } }),
      update: async (userId) => { writes += 1; return { userId, values: {} }; },
    });
    expect(writes).toBe(0);
    expect(getPortablePreference("theme", "system")).toBe("dark");
    expect(getPortablePreference("clockFormat", "24h")).toBe("12h");
    clearPortablePreferences();
  });

  test("discards a delayed response after locking and reopening an owner with the same ID", async () => {
    let resolveOld!: (value: { userId: number; values: Record<string, unknown> }) => void;
    const old = hydratePortablePreferences(0, {
      get: () => new Promise((resolve) => { resolveOld = resolve; }),
      update: async (userId) => ({ userId, values: {} }),
    });
    clearPortablePreferences();
    await hydratePortablePreferences(0, {
      get: async (userId) => ({ userId, values: { theme: "light" } }),
      update: async (userId) => ({ userId, values: {} }),
    });
    resolveOld({ userId: 0, values: { theme: "dark" } });
    await old;
    expect(getPortablePreference("theme", "system")).toBe("light");
    clearPortablePreferences();
  });

  test("does not send a stale queued update into a new unlock session", async () => {
    const previous = api.preferences.update;
    let writes = 0;
    api.preferences.update = async (userId) => { writes += 1; return { userId, values: {} }; };
    try {
      await hydratePortablePreferences(0, {
        get: async (userId) => ({ userId, values: {} }),
        update: async (userId) => ({ userId, values: {} }),
      });
      setPortablePreference("theme", "dark");
      clearPortablePreferences();
      await Promise.resolve();
      await Promise.resolve();
      expect(writes).toBe(0);
      expect(getPortablePreference("theme", "system")).toBe("system");
    } finally {
      api.preferences.update = previous;
      clearPortablePreferences();
    }
  });
});
