// Agent orchestrator — thin entry point that wires together the
// config, cache, message-persistence, and streaming layers.

import { HumanMessage, AIMessage } from "@langchain/core/messages";
import type { BaseMessage } from "@langchain/core/messages";
import { getAgentRunnableConfig, resetAgentPersistence } from "./checkpointer";
import { getSoulPromptForUser } from "./prompt";
import { loadMemoryContext } from "./context";
import { getAgentConfig } from "./config";
import { getOrBuildAgent } from "./cache";
import {
  getCurrentTurnMessages,
  collectToolsUsed,
  saveMessage,
  persistAssistantTurn,
} from "./messages";

export { invalidateSoulCache } from "./cache";

// ── Public types ─────────────────────────────────────────────────

export interface AgentResult {
  response: string;
  model: string;
  provider: string;
  toolsUsed: string[];
}

// ── Runtime resolver ─────────────────────────────────────────────

async function resolveAgentRuntime(userId: number, conversationHint?: string) {
  const config = await getAgentConfig(userId);
  const runnableConfig = await getAgentRunnableConfig(userId);

  const basePrompt =
    config.systemPrompt || (await getSoulPromptForUser(userId));
  const memoryContext = await loadMemoryContext(userId, conversationHint);
  const fullPrompt = memoryContext
    ? `${basePrompt}\n\n${memoryContext}`
    : basePrompt;
  const agent = getOrBuildAgent(config, userId, fullPrompt);

  return { config, runnableConfig, agent };
}

// ── Invoke (non-streaming) ───────────────────────────────────────

export async function runAgent(
  userMessage: string,
  userId: number,
): Promise<AgentResult> {
  const { config, runnableConfig, agent } = await resolveAgentRuntime(userId);
  await saveMessage(userId, "user", userMessage);

  const result = await agent.invoke(
    { messages: [new HumanMessage(userMessage)] },
    runnableConfig,
  );

  const aiMessages = result.messages.filter((m: BaseMessage) =>
    AIMessage.isInstance(m),
  );
  const lastAI = aiMessages[aiMessages.length - 1];
  const responseText =
    typeof lastAI?.content === "string"
      ? lastAI.content
      : JSON.stringify(lastAI?.content) || "[no response]";

  const toolsUsed = collectToolsUsed(
    getCurrentTurnMessages(result.messages, userMessage),
  );

  await persistAssistantTurn(userId, config, userMessage, responseText);

  return {
    response: responseText,
    model: config.model,
    provider: config.provider,
    toolsUsed,
  };
}

// ── Streaming variant ────────────────────────────────────────────

export async function* streamAgent(
  userMessage: string,
  userId: number,
): AsyncGenerator<string> {
  const { config, runnableConfig, agent } = await resolveAgentRuntime(
    userId,
    userMessage,
  );
  await saveMessage(userId, "user", userMessage);

  let fullResponse = "";

  const stream = await agent.stream(
    { messages: [new HumanMessage(userMessage)] },
    { ...runnableConfig, streamMode: "messages" },
  );

  for await (const [message, metadata] of stream) {
    if (
      AIMessage.isInstance(message) &&
      typeof message.content === "string" &&
      message.content &&
      metadata?.langgraph_node === "model_request"
    ) {
      fullResponse += message.content;
      yield message.content;
    }
  }

  const responseText = fullResponse || "[no response]";
  await persistAssistantTurn(userId, config, userMessage, responseText);
}

// ── Thread reset ─────────────────────────────────────────────────

export async function resetAgentThread(userId: number): Promise<void> {
  await resetAgentPersistence(userId);
}
