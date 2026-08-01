import { beforeEach, describe, expect, test } from "bun:test";
import type { Greeting } from "@anima/api-client";

import { setUnlockToken, clearUnlockToken } from "../src/lib/api";
import {
  ambientConsentAllows,
  getCachedGreeting,
  peekOneShotGreeting,
  purgeGreetingStorage,
  setCachedGreeting,
  stashOneShotGreeting,
  takeOneShotGreeting,
} from "../src/lib/greetingCache";

// bun:test has no DOM — a minimal sessionStorage stub is enough for these
// pure storage helpers (they already try/catch around every access).
const store = new Map<string, string>();
(globalThis as { sessionStorage?: unknown }).sessionStorage = {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, v),
  removeItem: (k: string) => void store.delete(k),
};

function greeting(overrides: Partial<Greeting> = {}): Greeting {
  return {
    message: "hello",
    llmGenerated: true,
    context: {
      currentFocus: null,
      openTaskCount: 0,
      overdueTasks: 0,
      daysSinceLastChat: 1,
      upcomingDeadlines: [],
    },
    ...overrides,
  };
}

beforeEach(() => {
  store.clear();
  setUnlockToken("unlocked");
});

describe("greeting TTL cache (IL-010 / PR #130)", () => {
  test("ordinary LLM greetings cache and replay", () => {
    setCachedGreeting(7, greeting());
    expect(getCachedGreeting(7)?.greeting.message).toBe("hello");
  });

  test("a dream-bearing greeting is NEVER cached — one-shot by contract", () => {
    setCachedGreeting(7, greeting()); // pre-existing cache entry
    setCachedGreeting(7, greeting({ ambientDream: true, message: "dreamy" }));
    // Not only is the dream greeting absent; the stale entry is cleared so
    // nothing replays over the freshly voiced dream.
    expect(getCachedGreeting(7)).toBeNull();
  });

  test("static fallbacks are not cached", () => {
    setCachedGreeting(7, greeting({ llmGenerated: false }));
    expect(getCachedGreeting(7)).toBeNull();
  });
});

describe("one-shot handoff slot (IL-010 / PR #130)", () => {
  test("a stashed greeting is taken exactly once", () => {
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "dreamy" }));
    expect(takeOneShotGreeting(7)?.message).toBe("dreamy");
    expect(takeOneShotGreeting(7)).toBeNull(); // consumed
  });

  test("a different user's mount never takes the stash", () => {
    stashOneShotGreeting(7, greeting({ ambientDream: true }));
    expect(takeOneShotGreeting(8)).toBeNull();
  });
});

describe("one-shot queue + consent (PR #130 round 3)", () => {
  test("two in-flight consumptions both survive — FIFO, nothing overwritten", () => {
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "first dream" }));
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "second dream" }));
    expect(takeOneShotGreeting(7)?.message).toBe("first dream");
    expect(takeOneShotGreeting(7)?.message).toBe("second dream");
    expect(takeOneShotGreeting(7)).toBeNull();
  });

  test("peek reports presence without consuming", () => {
    expect(peekOneShotGreeting(7)).toBe(false);
    stashOneShotGreeting(7, greeting({ ambientDream: true }));
    expect(peekOneShotGreeting(7)).toBe(true);
    expect(peekOneShotGreeting(7)).toBe(true); // still there
    expect(takeOneShotGreeting(7)).not.toBeNull();
    expect(peekOneShotGreeting(7)).toBe(false);
  });

  test("consent gate: ambient required, opt-out and master-off win", () => {
    expect(ambientConsentAllows({ enabled: true, dreamSharing: "ambient" })).toBe(true);
    expect(ambientConsentAllows({ dreamSharing: "ambient" })).toBe(true);
    expect(ambientConsentAllows({ enabled: false, dreamSharing: "ambient" })).toBe(false);
    expect(ambientConsentAllows({ enabled: true, dreamSharing: "on_ask" })).toBe(false);
    expect(ambientConsentAllows({ enabled: true, dreamSharing: "off" })).toBe(false);
  });
});

describe("session-bound plaintext handling (PR #130 round 8)", () => {
  test("purge clears pending handoffs and the cache", () => {
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "dreamy" }));
    setCachedGreeting(7, greeting());
    purgeGreetingStorage();
    expect(peekOneShotGreeting(7)).toBe(false);
    expect(getCachedGreeting(7)).toBeNull();
  });

  test("a late callback cannot repopulate plaintext after the session ends", () => {
    // Greeting request resolves AFTER logout/lock: the stash must refuse,
    // or decrypted autobiographical memory would sit in a locked webview's
    // sessionStorage with no TTL.
    clearUnlockToken();
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "dreamy" }));
    expect(peekOneShotGreeting(7)).toBe(false);
  });

  test("stashing works normally while the session is unlocked", () => {
    setUnlockToken("unlocked");
    stashOneShotGreeting(7, greeting({ ambientDream: true, message: "dreamy" }));
    expect(takeOneShotGreeting(7)?.message).toBe("dreamy");
  });
});

describe("origin-session binding (PR #130 round 9)", () => {
  test("a late callback cannot write into a REPLACEMENT session", () => {
    // User A's greeting is in flight; A logs out and B signs in before it
    // resolves. A bare truthiness check accepted B's token and would have
    // written A's decrypted dream into B's sessionStorage.
    setUnlockToken("token-A");
    const originToken = "token-A";
    setUnlockToken("token-B"); // A logged out, B signed in
    stashOneShotGreeting(7, greeting({ ambientDream: true }), originToken);
    expect(peekOneShotGreeting(7)).toBe(false);
  });

  test("the originating session may still stash", () => {
    setUnlockToken("token-A");
    stashOneShotGreeting(7, greeting({ ambientDream: true }), "token-A");
    expect(peekOneShotGreeting(7)).toBe(true);
  });

  test("no live session refuses regardless of origin", () => {
    clearUnlockToken();
    stashOneShotGreeting(7, greeting({ ambientDream: true }), null);
    expect(peekOneShotGreeting(7)).toBe(false);
  });
});
