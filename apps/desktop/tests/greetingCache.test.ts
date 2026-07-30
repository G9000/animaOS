import { beforeEach, describe, expect, test } from "bun:test";
import type { Greeting } from "@anima/api-client";

import {
  getCachedGreeting,
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

beforeEach(() => store.clear());

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
