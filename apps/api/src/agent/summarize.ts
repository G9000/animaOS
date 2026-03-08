import {
  SystemMessage,
  HumanMessage,
  RemoveMessage,
} from "@langchain/core/messages";
import type { BaseMessage } from "@langchain/core/messages";
import type { ProviderConfig } from "../llm/types";
import { createModel } from "./models";
import { loadMemoryContext } from "./context";
import { getAgentRunnableConfig, langGraphCheckpointer } from "./checkpointer";
import { getSoulPromptForUser } from "./prompt";
import { getOrBuildAgent } from "./cache";
import { KEEP_RECENT_MESSAGES, SUMMARIZE_THRESHOLD } from "./builder";

// ── Thread summarization ─────────────────────────────────────────
// When the checkpoint message list grows beyond SUMMARIZE_THRESHOLD,
// condense older messages into a summary and remove them from state.
// This keeps the checkpoint lean for long-lived conversations.

const SUMMARIZE_PROMPT = `You are a conversation summarizer. Given the conversation messages below, produce a concise summary that preserves:
- Key facts the user shared
- Decisions made or preferences expressed
- Open questions or tasks
- Emotional tone / relationship context

Be concise but thorough. Output only the summary text, no preamble.`;

export async function maybeSummarizeThread(
  userId: number,
  config: ProviderConfig & { systemPrompt?: string },
): Promise<void> {
  const runnableConfig = await getAgentRunnableConfig(userId);
  const checkpoint = await langGraphCheckpointer.getTuple(runnableConfig);
  if (!checkpoint) return;

  const state = checkpoint.checkpoint;
  const channelValues = state.channel_values as
    | Record<string, unknown>
    | undefined;
  const messages = (channelValues?.messages ?? []) as BaseMessage[];

  if (messages.length <= SUMMARIZE_THRESHOLD) return;

  // Split: messages to summarize vs. recent messages to keep
  const toSummarize = messages.slice(0, -KEEP_RECENT_MESSAGES);

  // Build the summary
  const model = createModel(config);
  const summaryResult = await model.invoke([
    new SystemMessage(SUMMARIZE_PROMPT),
    ...toSummarize,
    new HumanMessage("Summarize the conversation above."),
  ]);

  const summaryText =
    typeof summaryResult.content === "string"
      ? summaryResult.content
      : JSON.stringify(summaryResult.content);

  // Get the cached agent so we can update state via the graph
  const basePrompt = config.systemPrompt || (await getSoulPromptForUser(userId));
  const memoryContext = await loadMemoryContext(userId);
  const fullPrompt = memoryContext
    ? `${basePrompt}\n\n${memoryContext}`
    : basePrompt;
  const agent = getOrBuildAgent(config, userId, fullPrompt);

  // Remove old messages and prepend a summary system message
  const removeOps = toSummarize
    .filter((m) => m.id)
    .map((m) => new RemoveMessage({ id: m.id! }));

  const summaryMessage = new SystemMessage(
    `[Summary of earlier conversation]\n${summaryText}`,
  );

  await agent.invoke(
    { messages: [...removeOps, summaryMessage] },
    runnableConfig,
  );

  console.log(
    `[agent] Summarized thread for user ${userId}: ${toSummarize.length} messages condensed`,
  );
}
