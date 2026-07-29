import type { PendingInitiative } from "@anima/api-client";

export interface InitiativePollerDeps {
  fetchInitiatives: () => Promise<PendingInitiative[]>;
  ackInitiative: (id: number) => Promise<unknown>;
  onChange: (pending: PendingInitiative[]) => void;
  /** Default 60_000. */
  intervalMs?: number;
  /** Injectable for tests. */
  setIntervalFn?: typeof setInterval;
  clearIntervalFn?: typeof clearInterval;
}

export interface InitiativePoller {
  start(): void;
  stop(): void;
  pollNow(): Promise<void>;
  ack(id: number): Promise<void>;
}

/**
 * Polls the pending-initiative endpoint (the server marks fetched rows
 * `delivered`) and holds the client-side pending list. Acknowledge is a
 * user action: `ack()` removes the row locally even if the API call fails —
 * the server is the source of truth, so an unacked row simply comes back on
 * the next poll.
 */
export function createInitiativePoller(
  deps: InitiativePollerDeps,
): InitiativePoller {
  const intervalMs = deps.intervalMs ?? 60_000;
  const setIntervalFn = deps.setIntervalFn ?? setInterval;
  const clearIntervalFn = deps.clearIntervalFn ?? clearInterval;
  let timer: ReturnType<typeof setInterval> | null = null;
  let inFlight = false;
  let stopped = false;
  let pending: PendingInitiative[] = [];
  let generation = 0;
  // Tombstones for acked rows: a poll that starts DURING a slow ack captures
  // the post-bump generation, can read the row before the ack POST commits,
  // and would otherwise restore it. Tombstoned ids are filtered from every
  // poll result; a failed ack removes its tombstone so the next poll
  // re-serves the row (the server is still the source of truth).
  const acked = new Set<number>();

  const pollNow = async (): Promise<void> => {
    if (inFlight) return;
    inFlight = true;
    const gen = generation;
    try {
      const result = await deps.fetchInitiatives();
      if (generation === gen) {
        pending = result.filter((rowItem) => !acked.has(rowItem.id));
        deps.onChange(pending);
      }
    } catch {
      // Best-effort poll: a locked session or unreachable server must stay
      // silent; the next tick retries.
    } finally {
      inFlight = false;
    }
  };

  return {
    start() {
      if (timer !== null) return;
      stopped = false;
      timer = setIntervalFn(() => {
        void pollNow();
      }, intervalMs);
      void pollNow();
    },
    stop() {
      // Invalidate any in-flight poll (same mechanism `ack()` uses) so a
      // fetch started before `stop()` never reports stale/foreign data via
      // `onChange` after the caller has walked away (user switch, unmount).
      // `stopped` additionally suppresses the CONTINUATION of an ack that
      // was already awaiting its POST when we stopped — the replacement
      // poller shares the same React setter, so a late onChange from the old
      // instance could erase the new user's initiatives or expose the old
      // user's rows.
      stopped = true;
      generation += 1;
      if (timer !== null) {
        clearIntervalFn(timer);
        timer = null;
      }
    },
    pollNow,
    async ack(id: number) {
      generation += 1;
      acked.add(id);
      try {
        await deps.ackInitiative(id);
      } catch {
        // The server still holds the row; drop the tombstone so the next
        // poll re-serves it.
        acked.delete(id);
      }
      if (stopped) return; // stopped mid-POST: never notify a walked-away caller
      pending = pending.filter((rowItem) => rowItem.id !== id);
      deps.onChange(pending);
    },
  };
}

export interface PresenceGate {
  enabled: boolean;
  initiativeEnabled: boolean;
  quietHoursStart: number | null;
  quietHoursEnd: number | null;
}

// Same semantics as the server's `_in_quiet_hours`: inactive unless both
// ends are set and differ; wraps past midnight when start > end.
function inQuietHours(
  hour: number,
  start: number | null,
  end: number | null,
): boolean {
  if (start == null || end == null || start === end) return false;
  if (start < end) return start <= hour && hour < end;
  return hour >= start || hour < end;
}

/**
 * Wraps an initiatives fetch behind the user's CURRENT presence config.
 * The server's list operation is the consent authority (it re-checks
 * opt-in AND quiet hours atomically with the delivered side effect); this
 * client-side gate is the UX layer on top: it skips the initiatives
 * request entirely when opted out or inside the quiet-hours window, and
 * returns [], clearing any initiative already on screen within one cycle.
 */
export function createGatedInitiativeFetch(deps: {
  getPresenceGate: () => Promise<PresenceGate>;
  fetchInitiatives: () => Promise<PendingInitiative[]>;
  /** Injectable for tests; defaults to the local wall-clock hour. */
  getCurrentHour?: () => number;
}): () => Promise<PendingInitiative[]> {
  const getCurrentHour = deps.getCurrentHour ?? (() => new Date().getHours());
  return async () => {
    const gate = await deps.getPresenceGate();
    if (!gate.enabled || !gate.initiativeEnabled) return [];
    if (inQuietHours(getCurrentHour(), gate.quietHoursStart, gate.quietHoursEnd)) {
      return [];
    }
    return deps.fetchInitiatives();
  };
}
