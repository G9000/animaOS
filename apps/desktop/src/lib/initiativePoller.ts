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
  let pending: PendingInitiative[] = [];
  let generation = 0;

  const pollNow = async (): Promise<void> => {
    if (inFlight) return;
    inFlight = true;
    const gen = generation;
    try {
      const result = await deps.fetchInitiatives();
      if (generation === gen) {
        pending = result;
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
      timer = setIntervalFn(() => {
        void pollNow();
      }, intervalMs);
      void pollNow();
    },
    stop() {
      // Invalidate any in-flight poll (same mechanism `ack()` uses) so a
      // fetch started before `stop()` never reports stale/foreign data via
      // `onChange` after the caller has walked away (user switch, unmount).
      generation += 1;
      if (timer !== null) {
        clearIntervalFn(timer);
        timer = null;
      }
    },
    pollNow,
    async ack(id: number) {
      generation += 1;
      try {
        await deps.ackInitiative(id);
      } catch {
        // The server still holds the row; the next poll re-serves it.
      }
      pending = pending.filter((rowItem) => rowItem.id !== id);
      deps.onChange(pending);
    },
  };
}
