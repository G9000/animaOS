import { describe, expect, test } from "bun:test";
import {
  DEVICE_BACKGROUND_MEDIA_KEY,
  DEVICE_BGM_TRACKS_KEY,
  getPortablePreference,
  hydratePortablePreferences,
} from "../src/lib/portablePreferences";

class MemoryStorage implements Storage {
  private readonly data = new Map<string, string>();

  get length(): number {
    return this.data.size;
  }

  clear(): void {
    this.data.clear();
  }

  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.data.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.data.delete(key);
  }

  setItem(key: string, value: string): void {
    this.data.set(key, value);
  }
}

function preferenceApi(
  onUpdate?: (values: Record<string, unknown>) => void,
) {
  let values: Record<string, unknown> = {};
  return {
    get: async (userId: number) => ({ userId, values }),
    update: async (userId: number, patch: Record<string, unknown>) => {
      onUpdate?.(patch);
      values = { ...values, ...patch };
      return { userId, values };
    },
  };
}

describe("portable preference migration", () => {
  test("verifies Core values before removing exact legacy sources", async () => {
    const storage = new MemoryStorage();
    storage.setItem("anima-theme", "dark");
    storage.setItem("anima_clock_format", "12h");
    storage.setItem(
      "anima-background-config",
      JSON.stringify({ type: "gradient", value: "linear-gradient(#000,#fff)" }),
    );

    await hydratePortablePreferences(7, preferenceApi(), storage);

    expect(storage.getItem("anima-theme")).toBeNull();
    expect(storage.getItem("anima_clock_format")).toBeNull();
    expect(storage.getItem("anima-background-config")).toBeNull();
    expect(getPortablePreference("theme", "system")).toBe("dark");
    expect(getPortablePreference("clockFormat", "24h")).toBe("12h");
  });

  test("retries a newer value instead of deleting it as a stale handoff", async () => {
    const storage = new MemoryStorage();
    storage.setItem("anima-theme", "dark");
    let calls = 0;
    const api = preferenceApi(() => {
      calls += 1;
      if (calls === 1) storage.setItem("anima-theme", "light");
    });

    await hydratePortablePreferences(8, api, storage);

    expect(calls).toBe(2);
    expect(storage.getItem("anima-theme")).toBeNull();
    expect(getPortablePreference("theme", "system")).toBe("light");
  });

  test("retains legacy sources when the encrypted response does not match", async () => {
    const storage = new MemoryStorage();
    storage.setItem("anima-theme", "dark");
    const api = {
      get: async (userId: number) => ({ userId, values: {} }),
      update: async (userId: number) => ({ userId, values: { theme: "light" } }),
    };

    await expect(hydratePortablePreferences(10, api, storage)).rejects.toThrow(
      "verification failed",
    );
    expect(storage.getItem("anima-theme")).toBe("dark");
  });

  test("moves host media metadata to verified device-local keys", async () => {
    const storage = new MemoryStorage();
    const background = JSON.stringify({ type: "image", value: "private.png" });
    storage.setItem("anima-background-config", background);
    storage.setItem(
      "anima_bgm_state",
      JSON.stringify({
        currentId: "user-1",
        muted: true,
        userTracks: [{ id: "user-1", name: "Local" }],
      }),
    );

    await hydratePortablePreferences(9, preferenceApi(), storage);

    expect(storage.getItem("anima-background-config")).toBeNull();
    expect(storage.getItem(DEVICE_BACKGROUND_MEDIA_KEY)).toBe(background);
    expect(storage.getItem("anima_bgm_state")).toBeNull();
    expect(storage.getItem(DEVICE_BGM_TRACKS_KEY)).toContain("user-1");
    expect(getPortablePreference<{ muted: boolean }>("bgm", { muted: false }).muted)
      .toBe(true);
  });
});
