import type { Greeting } from "@anima/api-client";

/**
 * Dashboard greeting cache (IL-010 / PR #130 review).
 *
 * Two storage slots with different lifetimes:
 * - The TTL cache replays an ordinary LLM greeting across Dashboard remounts
 *   for a few minutes. A greeting that voices a CONSUMED ambient dream is
 *   one-shot and is never cached (replaying it would re-voice the same
 *   surfaced dream despite consume-once semantics).
 * - The one-shot slot is the durable handoff for a dream-bearing greeting
 *   whose fetch resolves after the Dashboard unmounted: the dream was
 *   already consumed server-side, so discarding the response would silence
 *   it forever. The next mount takes it exactly once.
 */

export const GREETING_CACHE_KEY = "anima_dashboard_greeting";
export const GREETING_CACHE_TTL_MS = 5 * 60 * 1000;

export type CachedGreeting = { greeting: Greeting; ts: number; userId: number };

export function clearCachedGreeting(): void {
  try {
    sessionStorage.removeItem(GREETING_CACHE_KEY);
  } catch {
    /* ignore */
  }
}

export function getCachedGreeting(userId: number): CachedGreeting | null {
  try {
    const raw = sessionStorage.getItem(GREETING_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      parsed?.userId === userId &&
      parsed?.greeting?.llmGenerated === true &&
      typeof parsed.ts === "number" &&
      Date.now() - parsed.ts < GREETING_CACHE_TTL_MS
    )
      return parsed;
    clearCachedGreeting();
  } catch {
    /* ignore */
  }
  return null;
}

export function setCachedGreeting(userId: number, greeting: Greeting): void {
  try {
    // IL-010 (PR #130 review): a greeting that voices a consumed ambient
    // dream is ONE-SHOT — caching it would replay the same dream on every
    // Dashboard remount for the TTL despite consume-once semantics.
    if (!greeting.llmGenerated || greeting.ambientDream) {
      clearCachedGreeting();
      return;
    }
    sessionStorage.setItem(
      GREETING_CACHE_KEY,
      JSON.stringify({ greeting, ts: Date.now(), userId }),
    );
  } catch {
    /* ignore */
  }
}

// IL-010 (PR #130 review): durable handoff for a dream-bearing greeting that
// arrives after the Dashboard unmounted. The response has already consumed
// the dream server-side; discarding it would silence the dream forever, so
// the success handler stashes it here and the next Dashboard mount displays
// it exactly once.
export const GREETING_ONESHOT_KEY = "anima_dashboard_greeting_oneshot";

export function stashOneShotGreeting(userId: number, greeting: Greeting): void {
  try {
    sessionStorage.setItem(
      GREETING_ONESHOT_KEY,
      JSON.stringify({ greeting, userId }),
    );
  } catch {
    /* ignore */
  }
}

export function takeOneShotGreeting(userId: number): Greeting | null {
  try {
    const raw = sessionStorage.getItem(GREETING_ONESHOT_KEY);
    if (!raw) return null;
    sessionStorage.removeItem(GREETING_ONESHOT_KEY);
    const parsed = JSON.parse(raw) as { greeting?: Greeting; userId?: number };
    if (parsed?.userId === userId && parsed.greeting) return parsed.greeting;
  } catch {
    /* ignore */
  }
  return null;
}

