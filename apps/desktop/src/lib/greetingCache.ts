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

type OneShotEntry = { greeting: Greeting; userId: number };

function readOneShotQueue(): OneShotEntry[] {
  try {
    const raw = sessionStorage.getItem(GREETING_ONESHOT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as OneShotEntry[]) : [];
  } catch {
    return [];
  }
}

function writeOneShotQueue(queue: OneShotEntry[]): void {
  try {
    if (queue.length === 0) sessionStorage.removeItem(GREETING_ONESHOT_KEY);
    else sessionStorage.setItem(GREETING_ONESHOT_KEY, JSON.stringify(queue));
  } catch {
    /* ignore */
  }
}

// A QUEUE, not a slot (PR #130 review): rapid mount/unmount cycles can leave
// several greeting requests in flight, each having claimed a different dream
// server-side — a single slot let the second stash overwrite (and silence
// forever) the first consumed narrative.
export function stashOneShotGreeting(userId: number, greeting: Greeting): void {
  writeOneShotQueue([...readOneShotQueue(), { greeting, userId }]);
}

/** Whether a stashed greeting exists for `userId` — without consuming it
 * (the caller may need to verify consent before taking). */
export function peekOneShotGreeting(userId: number): boolean {
  return readOneShotQueue().some((entry) => entry.userId === userId);
}

export function takeOneShotGreeting(userId: number): Greeting | null {
  const queue = readOneShotQueue();
  const index = queue.findIndex((entry) => entry.userId === userId);
  if (index === -1) return null;
  const [entry] = queue.splice(index, 1);
  writeOneShotQueue(queue);
  return entry.greeting;
}

export function clearOneShotGreetings(userId: number): void {
  writeOneShotQueue(readOneShotQueue().filter((e) => e.userId !== userId));
}

/** IL-010 consent gate for replaying a stashed dream-bearing greeting: the
 * dream was consumed server-side, but an opt-out between the stash and the
 * next mount must win — the user asked for silence. */
export function ambientConsentAllows(config: {
  enabled?: boolean;
  dreamSharing?: string;
}): boolean {
  return config.enabled !== false && config.dreamSharing === "ambient";
}

