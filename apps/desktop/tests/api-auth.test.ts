import { afterEach, describe, expect, test } from "bun:test";

import {
  clearUnlockToken,
  fetchRuntimeWithNonceRefresh,
  getUnlockToken,
  setUnlockToken,
  UNLOCK_SESSION_LOCKED_EVENT,
} from "../src/lib/api";

const originalFetch = globalThis.fetch;
const originalSessionStorage = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
const originalLocalStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");

function installMemoryStorage(name: "sessionStorage" | "localStorage") {
  const store = new Map<string, string>();
  Object.defineProperty(globalThis, name, {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    },
  });
}

function restoreStorage(name: "sessionStorage" | "localStorage", descriptor?: PropertyDescriptor) {
  if (descriptor) {
    Object.defineProperty(globalThis, name, descriptor);
  } else {
    delete (globalThis as Record<string, unknown>)[name];
  }
}

describe("runtime auth handling", () => {
  afterEach(() => {
    globalThis.fetch = originalFetch;
    clearUnlockToken();
    restoreStorage("sessionStorage", originalSessionStorage);
    restoreStorage("localStorage", originalLocalStorage);
  });

  test("clears stale unlock token when the runtime reports a locked session", async () => {
    installMemoryStorage("sessionStorage");
    installMemoryStorage("localStorage");
    setUnlockToken("stale-token");

    let lockedEvents = 0;
    const onLocked = () => {
      lockedEvents += 1;
    };
    globalThis.addEventListener(UNLOCK_SESSION_LOCKED_EVENT, onLocked);

    globalThis.fetch = (async () =>
      Response.json(
        { detail: "Session locked. Please sign in again." },
        { status: 401 },
      )) as typeof fetch;

    try {
      const response = await fetchRuntimeWithNonceRefresh("https://api.test/api/presence/1");

      expect(response.status).toBe(401);
      expect(getUnlockToken()).toBeNull();
      expect(lockedEvents).toBe(1);
    } finally {
      globalThis.removeEventListener(UNLOCK_SESSION_LOCKED_EVENT, onLocked);
    }
  });

});
