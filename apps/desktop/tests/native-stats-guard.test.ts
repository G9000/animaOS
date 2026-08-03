import { afterEach, describe, expect, test } from "bun:test";

import { isTauri } from "../src/lib/isTauri";
import { fetchSystemStats } from "../src/hooks/useSystemStats";
import { fetchNetworkStats } from "../src/hooks/useNetworkStats";

const originalWindow = (globalThis as { window?: unknown }).window;

function setWindow(value: unknown) {
  (globalThis as { window?: unknown }).window = value;
}

afterEach(() => {
  setWindow(originalWindow);
});

describe("isTauri", () => {
  test("is false in a plain browser window (dev:web)", () => {
    setWindow({});
    expect(isTauri()).toBe(false);
  });

  test("is true when the Tauri host injected its internals", () => {
    setWindow({ __TAURI_INTERNALS__: {} });
    expect(isTauri()).toBe(true);
  });

  test("is false when there is no window at all", () => {
    setWindow(undefined);
    expect(isTauri()).toBe(false);
  });
});

describe("native stats fetchers outside Tauri", () => {
  test("fetchSystemStats resolves null without invoking the native command", async () => {
    setWindow({});
    let calls = 0;
    const invokeSpy = async () => {
      calls += 1;
      throw new Error("invoke must not be reached in web mode");
    };

    await expect(fetchSystemStats(invokeSpy)).resolves.toBeNull();
    expect(calls).toBe(0);
  });

  test("fetchNetworkStats resolves null without invoking the native command", async () => {
    setWindow({});
    let calls = 0;
    const invokeSpy = async () => {
      calls += 1;
      throw new Error("invoke must not be reached in web mode");
    };

    await expect(fetchNetworkStats(invokeSpy)).resolves.toBeNull();
    expect(calls).toBe(0);
  });
});

describe("native stats fetchers inside Tauri", () => {
  test("fetchSystemStats returns the native payload", async () => {
    setWindow({ __TAURI_INTERNALS__: {} });
    const payload = {
      cpu_usage: 12,
      cpu_temp_c: null,
      ram_used_mb: 2048,
      ram_total_mb: 16384,
      app_ram_mb: 256,
      gpu: {
        name: null,
        usage: null,
        temp_c: null,
        vram_used_mb: null,
        vram_total_mb: null,
      },
    };

    await expect(fetchSystemStats(async () => payload)).resolves.toEqual(payload);
  });

  test("fetchNetworkStats returns the native payload", async () => {
    setWindow({ __TAURI_INTERNALS__: {} });
    const payload = { download_kbps: 128, upload_kbps: 64 };

    await expect(fetchNetworkStats(async () => payload)).resolves.toEqual(payload);
  });

  test("a failing native command is swallowed into null", async () => {
    setWindow({ __TAURI_INTERNALS__: {} });
    const boom = async () => {
      throw new Error("command unavailable");
    };

    await expect(fetchSystemStats(boom)).resolves.toBeNull();
    await expect(fetchNetworkStats(boom)).resolves.toBeNull();
  });
});
