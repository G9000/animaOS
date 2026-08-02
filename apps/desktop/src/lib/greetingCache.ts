import type { Greeting } from "@anima/api-client";

import { getUnlockToken } from "./api";

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

// IL-010 (PR #130 review): handoff for a dream-bearing greeting that arrives
// after the Dashboard unmounted. The dream is CLAIMED server-side, so the
// success handler stashes the response here and the next Dashboard mount
// displays it exactly once — but only while the claim is still live
// (IL-015 / PR #135 review): past the claim deadline the server may offer
// the same dream through another channel, so the stash is dropped instead.
export const GREETING_ONESHOT_KEY = "anima_dashboard_greeting_oneshot";

/**
 * Fallback lifetime for a stashed dream whose response carried no
 * `ambientDreamExpiresAt` (PR #135 review) — an older server, or a field
 * lost in transit. Deliberately conservative: the real deadline is the
 * server's `dream_claim_ttl_minutes` (default 10), and expiring a shade
 * early only costs a re-offer, while expiring late risks voicing the same
 * dream through two channels.
 */
export const ONESHOT_FALLBACK_TTL_MS = 10 * 60 * 1000;

type OneShotEntry = {
  greeting: Greeting;
  userId: number;
  /** Epoch ms after which this entry must not be displayed — the server's
   * claim deadline. Entries stashed before this field existed have none and
   * are treated as already expired rather than shown unbounded. */
  expiresAt?: number;
};

/** Epoch ms at which a stashed dream-bearing greeting stops being safe to
 * show. The server states the deadline because the TTL is its
 * configuration; a malformed or missing value falls back to the stash time
 * plus {@link ONESHOT_FALLBACK_TTL_MS}. */
function oneShotExpiryFor(greeting: Greeting, now: number): number {
  const stated = greeting.ambientDreamExpiresAt;
  if (stated) {
    const parsed = Date.parse(stated);
    if (Number.isFinite(parsed)) return parsed;
  }
  return now + ONESHOT_FALLBACK_TTL_MS;
}

/** Whether a dream-bearing greeting arrived (or was held) past the deadline
 * of the claim behind it — after which the server may offer the same dream
 * through another channel, so this copy must not be voiced. Greetings
 * carrying no dream are never expired. */
export function dreamClaimExpired(
  greeting: Greeting,
  now: number = Date.now(),
): boolean {
  if (!greeting.ambientDream) return false;
  return oneShotExpiryFor(greeting, now) <= now;
}

function isLive(entry: OneShotEntry, now: number): boolean {
  return typeof entry.expiresAt === "number" && entry.expiresAt > now;
}

/** Reads the queue with expired entries dropped, persisting the prune.
 *
 * A stashed greeting outliving its server-side claim is a duplicate
 * disclosure, not just a stale UI (PR #135 review, P1): once the claim
 * expires the dream becomes offerable again, so an initiative or a fresh
 * greeting can voice it while this copy still sits in sessionStorage.
 * Dropping it is now cheap — under IL-015 an unacknowledged dream is NOT
 * consumed, so the server simply offers it again.
 */
function readOneShotQueue(): OneShotEntry[] {
  try {
    const raw = sessionStorage.getItem(GREETING_ONESHOT_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const now = Date.now();
    const live = (parsed as OneShotEntry[]).filter((entry) =>
      isLive(entry, now),
    );
    if (live.length !== parsed.length) writeOneShotQueue(live);
    return live;
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
export function stashOneShotGreeting(
  userId: number,
  greeting: Greeting,
  /** Unlock token captured when the greeting request STARTED. The stash is
   * refused unless it is still the live token (PR #130 review): a bare
   * truthiness check accepted a REPLACEMENT session, so a late callback
   * could write user A's decrypted dream into the sessionStorage of user B
   * who signed in meanwhile. Omitted only by callers with no request
   * context, which then require merely that some session is unlocked. */
  originUnlockToken?: string | null,
): void {
  const live = getUnlockToken();
  if (!live) return; // logged out or locked
  if (originUnlockToken !== undefined && originUnlockToken !== live) return;
  const now = Date.now();
  const expiresAt = oneShotExpiryFor(greeting, now);
  // Already past the claim deadline when it arrived (a very slow response):
  // storing it would only queue a duplicate disclosure.
  if (expiresAt <= now) return;
  writeOneShotQueue([
    ...readOneShotQueue(),
    { greeting, userId, expiresAt },
  ]);
}

/** Drop every pending handoff and the greeting cache — called when the
 * unlock session ends (logout or lock). The dream is already consumed
 * server-side; losing the handoff is the correct trade for not leaving
 * decrypted autobiographical memory in a locked webview's storage. */
export function purgeGreetingStorage(): void {
  try {
    sessionStorage.removeItem(GREETING_ONESHOT_KEY);
  } catch {
    /* ignore */
  }
  clearCachedGreeting();
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

