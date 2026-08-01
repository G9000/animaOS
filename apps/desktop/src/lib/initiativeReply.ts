import type { ChatContextMessage, PendingInitiative } from "@anima/api-client";

/**
 * IL-009 — the router state a Reply on a pending initiative hands to /chat.
 *
 * The chat page's seeded-thread contract (`ChatLocationState`) does the rest:
 * the initiative text renders as the opening assistant message of a fresh
 * thread, and on the user's first send it rides along as context messages
 * (`skipContextDisplay`, since it is already shown) — so the reply the user
 * types actually references what the companion said, visible to the user AND
 * available to the model, instead of relying on their memory of the
 * notification.
 */
export interface InitiativeReplyState {
  seedThread: true;
  contextMessages: ChatContextMessage[];
}

export function initiativeReplyState(
  initiative: Pick<PendingInitiative, "text">,
): InitiativeReplyState {
  return {
    seedThread: true,
    contextMessages: [
      // Verbatim — the seed must be exactly what the overlay showed, never a
      // paraphrase, so the conversation starts from the real message.
      { role: "assistant", content: initiative.text, source: "initiative" },
    ],
  };
}

/**
 * How an already-mounted Chat should treat a navigation (PR #131 review).
 *
 * Mount-time refs capture `location.state` exactly once, so a seedThread
 * navigation that lands while Chat is already on-screen (the initiative
 * overlay is global) must be applied explicitly — and exactly once per
 * navigation (`location.key`). Mid-stream arrivals are deferred rather than
 * applied (swapping the thread under an active stream corrupts the view) or
 * dropped (the ack already happened; dropping loses the text — the original
 * bug wearing a new face).
 */
export type SeedNavigationAction = "ignore" | "apply" | "defer";

export function classifySeedNavigation(options: {
  handledKey: string;
  key: string;
  seedThread: boolean;
  contextCount: number;
  streaming: boolean;
}): SeedNavigationAction {
  if (options.key === options.handledKey) return "ignore"; // mount path owns it
  if (!options.seedThread || options.contextCount === 0) return "ignore";
  return options.streaming ? "defer" : "apply";
}

/**
 * Accumulate seed contexts instead of replacing them (PR #131 review round
 * 2). Every acked initiative's text must survive: the overlay supports
 * multiple pending rows, so a second Reply — while a stream defers the
 * first, or while the first seeded thread is still unsent — must ADD its
 * message, not overwrite the previous one. Each context renders as its own
 * assistant bubble in the seeded thread.
 */
export function mergeSeedContexts(
  existing: ChatContextMessage[] | null,
  next: ChatContextMessage[],
): ChatContextMessage[] {
  return [...(existing ?? []), ...next];
}

/**
 * What abandoning a seeded reply should do with its owed thread close
 * (PR #131 round 8).
 *
 * Re-issuing `close` while the eager close is still in flight sends a second
 * concurrent POST for the same thread — on PostgreSQL both sessions can see
 * it active and each schedule `on_thread_close()`, duplicating episode
 * generation and archival. So: reuse the in-flight request when one exists
 * (retrying only if it definitively failed), skip entirely when the user is
 * re-opening the very thread the close targets, and otherwise fire one
 * best-effort close.
 */
export type SeedCloseAbandonAction = "none" | "await-inflight" | "close";

export function classifySeedCloseAbandon(options: {
  pendingThreadId: number | null;
  keepThreadId?: number;
  /** Thread the in-flight close request targets, if any. Reuse is only
   * legal when it matches the pending thread (PR #131 round 10): a promise
   * for thread A must never settle — or fail — on behalf of thread B. */
  inFlightThreadId?: number | null;
}): SeedCloseAbandonAction {
  const { pendingThreadId, keepThreadId, inFlightThreadId } = options;
  if (pendingThreadId == null) return "none";
  if (pendingThreadId === keepThreadId) return "none";
  return inFlightThreadId === pendingThreadId ? "await-inflight" : "close";
}
