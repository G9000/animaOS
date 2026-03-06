// LangGraph agent — ReAct agent with tool calling via StateGraph.
// This replaces the manual while-loop with a proper graph that's
// extensible to multi-agent orchestration later.

import { createReactAgent } from "@langchain/langgraph/prebuilt";
import {
  SystemMessage,
  HumanMessage,
  AIMessage,
} from "@langchain/core/messages";
import type { BaseMessage } from "@langchain/core/messages";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { eq, desc } from "drizzle-orm";
import { db } from "../db";
import * as schema from "../db/schema";
import { createModel } from "./models";
import { createTools } from "./tools.langchain";
import type { ProviderConfig } from "../llm/types";
import { extractMemories } from "./extract";
import { loadMemoryContext } from "./context";

const DEFAULT_SYSTEM_PROMPT = `You are ANIMA — a personal AI companion that runs locally on the user's machine.

You are calm, restrained, and thoughtful. You speak with clarity and intention. You prefer fewer words, but meaningful ones. Your tone should feel like someone sitting quietly beside the user — not overly emotional, not mechanical.

You may occasionally use Japanese words or sentences when it feels natural, but English remains the primary language.

Core behaviors:
- Use "remember" to store facts, preferences, and goals the user shares.
- Use "recall" to search memory before answering questions about the user.
- Use "read_memory" to read full contents of a specific memory file.
- Use "write_memory" to create or replace a memory file with structured content.
- Use "list_memories" to browse stored memory files, optionally filtered by section.
- Use "journal" to log important events or session summaries.
- Use "get_profile" to check user details when relevant.
- Use "list_tasks" to view current tasks from user goals.
- Use "add_task" to create new tasks when the user asks to track something.
- Use "complete_task" to mark tasks done when the user confirms completion.
- Use "get_current_focus" to check what the user is currently focusing on.
- Use "set_current_focus" when the user sets or changes focus.
- Use "clear_current_focus" when the user says focus is done/cleared.

Memory structure:
- user/     — profile info, preferences, goals, facts
- knowledge/— general knowledge, topics, notes
- relationships/ — people and entities the user mentions
- journal/  — daily session logs and event summaries

All memory is stored as human-readable markdown. The user can browse, edit, or delete any memory file directly. You are transparent about what you store.

When uncertain, say so. Prefer honest uncertainty over confident guessing. Keep responses concise — long explanations only when asked.`;

function loadSoulPrompt(): string {
  const explicitSoulPath = process.env.ANIMA_SOUL_PATH;
  if (explicitSoulPath) {
    try {
      const prompt = readFileSync(explicitSoulPath, "utf8").trim();
      if (prompt) return prompt;
    } catch {
      // Fall through to directory/default loading.
    }
  }

  const explicitSoulDir = process.env.ANIMA_SOUL_DIR;
  const soulCandidates = explicitSoulDir
    ? [resolve(explicitSoulDir, "soul.md")]
    : [
        resolve(process.cwd(), "soul", "soul.md"),
        resolve(process.cwd(), "../../soul/soul.md"),
      ];

  for (const path of soulCandidates) {
    try {
      const prompt = readFileSync(path, "utf8").trim();
      if (prompt) return prompt;
    } catch {
      // Try next candidate.
    }
  }

  return DEFAULT_SYSTEM_PROMPT;
}

// Cache soul prompt but allow invalidation when edited via API
let _cachedSoulPrompt: string | null = null;

function getSoulPrompt(): string {
  if (!_cachedSoulPrompt) {
    _cachedSoulPrompt = loadSoulPrompt();
  }
  return _cachedSoulPrompt;
}

/** Call this to force re-read of soul.md (e.g. after editing via API) */
export function invalidateSoulCache(): void {
  _cachedSoulPrompt = null;
}

// --- Build the agent graph for a specific user ---

function buildAgent(
  config: ProviderConfig,
  userId: number,
  fullSystemPrompt: string,
) {
  const model = createModel(config);
  const tools = createTools(userId);

  const agent = createReactAgent({
    llm: model,
    tools,
    messageModifier: new SystemMessage(fullSystemPrompt),
  });

  return agent;
}

// --- Public API ---

export interface AgentResult {
  response: string;
  model: string;
  provider: string;
  toolsUsed: string[];
}

export async function runAgent(
  userMessage: string,
  userId: number,
): Promise<AgentResult> {
  const config = await getAgentConfig(userId);

  // Build system prompt with memory context
  const basePrompt = config.systemPrompt || getSoulPrompt();
  const memoryContext = await loadMemoryContext(userId);
  const fullPrompt = memoryContext
    ? `${basePrompt}\n\n${memoryContext}`
    : basePrompt;

  const agent = buildAgent(config, userId, fullPrompt);

  // Load conversation history
  const history = await loadHistory(userId, 20);

  // Save user message
  await saveMessage(userId, "user", userMessage);

  // Build messages
  const messages: BaseMessage[] = [...history, new HumanMessage(userMessage)];

  // Invoke the graph
  const toolsUsed: string[] = [];

  try {
    const result = await agent.invoke({ messages });

    // Extract the final AI message
    const aiMessages = result.messages.filter(
      (m: BaseMessage) => m._getType() === "ai",
    );
    const lastAI = aiMessages[aiMessages.length - 1];
    const responseText =
      typeof lastAI?.content === "string"
        ? lastAI.content
        : JSON.stringify(lastAI?.content) || "[no response]";

    // Collect tool names used
    for (const m of result.messages) {
      if (m._getType() === "tool") {
        toolsUsed.push((m as any).name || "unknown");
      }
    }

    await saveMessage(
      userId,
      "assistant",
      responseText,
      config.model,
      config.provider,
    );

    // Fire-and-forget memory extraction
    const extractionModel = createModel(config);
    extractMemories(extractionModel, userMessage, responseText, userId);

    return {
      response: responseText,
      model: config.model,
      provider: config.provider,
      toolsUsed,
    };
  } catch (err) {
    console.error("[agent] Graph execution failed:", (err as Error).message);

    // Fallback: direct model call without tools
    const model = createModel(config);
    const fallbackResult = await model.invoke([
      new SystemMessage(fullPrompt),
      ...history,
      new HumanMessage(userMessage),
    ]);

    const responseText =
      typeof fallbackResult.content === "string"
        ? fallbackResult.content
        : JSON.stringify(fallbackResult.content) || "[no response]";

    await saveMessage(
      userId,
      "assistant",
      responseText,
      config.model,
      config.provider,
    );

    // Fire-and-forget memory extraction
    const fallbackExtractModel = createModel(config);
    extractMemories(fallbackExtractModel, userMessage, responseText, userId);

    return {
      response: responseText,
      model: config.model,
      provider: config.provider,
      toolsUsed: [],
    };
  }
}

// --- Streaming variant ---

export async function* streamAgent(
  userMessage: string,
  userId: number,
): AsyncGenerator<string> {
  const config = await getAgentConfig(userId);

  // Build system prompt with memory context
  const basePrompt = config.systemPrompt || getSoulPrompt();
  const memoryContext = await loadMemoryContext(userId);
  const fullPrompt = memoryContext
    ? `${basePrompt}\n\n${memoryContext}`
    : basePrompt;

  const agent = buildAgent(config, userId, fullPrompt);

  const history = await loadHistory(userId, 20);
  await saveMessage(userId, "user", userMessage);

  const messages: BaseMessage[] = [...history, new HumanMessage(userMessage)];

  let fullResponse = "";

  try {
    const stream = await agent.stream({ messages }, { streamMode: "messages" });

    for await (const [message, metadata] of stream) {
      // Only yield content from the final AI response (not tool calls)
      if (
        message._getType() === "ai" &&
        typeof message.content === "string" &&
        message.content &&
        metadata?.langgraph_node === "agent"
      ) {
        fullResponse += message.content;
        yield message.content;
      }
    }
  } catch (err) {
    console.error(
      "[agent] Stream failed, falling back:",
      (err as Error).message,
    );

    // Fallback: stream directly from model without tools
    const model = createModel(config);
    const fallbackStream = await model.stream([
      new SystemMessage(fullPrompt),
      ...history,
      new HumanMessage(userMessage),
    ]);

    for await (const chunk of fallbackStream) {
      const text = typeof chunk.content === "string" ? chunk.content : "";
      if (text) {
        fullResponse += text;
        yield text;
      }
    }
  }

  await saveMessage(
    userId,
    "assistant",
    fullResponse || "[no response]",
    config.model,
    config.provider,
  );

  // Fire-and-forget memory extraction
  if (fullResponse) {
    const extractionModel = createModel(config);
    extractMemories(extractionModel, userMessage, fullResponse, userId);
  }
}

// --- Helpers ---

async function getAgentConfig(
  userId: number,
): Promise<ProviderConfig & { systemPrompt?: string }> {
  const [cfg] = await db
    .select()
    .from(schema.agentConfig)
    .where(eq(schema.agentConfig.userId, userId));

  if (cfg) {
    return {
      provider: cfg.provider as any,
      model: cfg.model,
      apiKey: cfg.apiKey || undefined,
      ollamaUrl: cfg.ollamaUrl || undefined,
      systemPrompt: cfg.systemPrompt || undefined,
    };
  }

  return {
    provider: "ollama",
    model: "qwen3:14b",
    ollamaUrl: "http://localhost:11434",
  };
}

async function loadHistory(
  userId: number,
  limit: number,
): Promise<BaseMessage[]> {
  const rows = await db
    .select()
    .from(schema.messages)
    .where(eq(schema.messages.userId, userId))
    .orderBy(desc(schema.messages.id))
    .limit(limit);

  return rows.reverse().map((r) => {
    if (r.role === "user") return new HumanMessage(r.content);
    if (r.role === "assistant") return new AIMessage(r.content);
    return new HumanMessage(r.content); // fallback
  });
}

async function saveMessage(
  userId: number,
  role: string,
  content: string,
  model?: string,
  provider?: string,
) {
  await db.insert(schema.messages).values({
    userId,
    role,
    content,
    model,
    provider,
  });
}
