import { HumanMessage, ToolMessage } from "@langchain/core/messages";
import type { BaseMessage } from "@langchain/core/messages";
import { db } from "../db";
import * as schema from "../db/schema";
import type { ProviderConfig } from "../llm/types";
import { createModel } from "./models";
import { extractMemories } from "./extract";
import { maybeSummarizeThread } from "./summarize";
import { maybeEncryptForUser } from "../lib/data-crypto";

// ── Message helpers ──────────────────────────────────────────────

/**
 * Walk backward through the message list and return everything after
 * the last `HumanMessage` whose content equals `userMessage`.
 */
export function getCurrentTurnMessages(
  messages: BaseMessage[],
  userMessage: string,
): BaseMessage[] {
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    if (!HumanMessage.isInstance(message)) continue;
    if (typeof message.content !== "string") continue;
    if (message.content !== userMessage) continue;
    return messages.slice(i + 1);
  }
  return messages;
}

/** Collect tool names from the current turn's messages. */
export function collectToolsUsed(turnMessages: BaseMessage[]): string[] {
  const tools: string[] = [];
  for (const m of turnMessages) {
    if (ToolMessage.isInstance(m)) {
      tools.push(m.name || "unknown");
    }
  }
  return tools;
}

// ── Persistence ──────────────────────────────────────────────────

export async function saveMessage(
  userId: number,
  role: string,
  content: string,
  model?: string,
  provider?: string,
) {
  await db.insert(schema.messages).values({
    userId,
    role,
    content: maybeEncryptForUser(userId, content),
    model,
    provider,
  });
}

/**
 * Save the assistant reply, then fire-and-forget memory extraction
 * and thread summarization.
 */
export async function persistAssistantTurn(
  userId: number,
  config: ProviderConfig,
  userMessage: string,
  responseText: string,
): Promise<void> {
  await saveMessage(
    userId,
    "assistant",
    responseText,
    config.model,
    config.provider,
  );

  if (!responseText || responseText === "[no response]") return;

  // Fire-and-forget memory extraction.
  const extractionModel = createModel(config);
  extractMemories(extractionModel, userMessage, responseText, userId).catch(
    (err) =>
      console.error("[agent] Memory extraction failed:", (err as Error).message),
  );

  // Fire-and-forget: summarize old messages if checkpoint is growing large.
  maybeSummarizeThread(userId, config).catch((err) =>
    console.error("[agent] Summarization failed:", (err as Error).message),
  );
}
