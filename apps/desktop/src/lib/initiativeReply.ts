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
