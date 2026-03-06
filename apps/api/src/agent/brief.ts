// Daily brief generator — produces a short briefing from memory context.
// Called on dashboard load to give ANIMA a proactive voice.

import { SystemMessage, HumanMessage } from "@langchain/core/messages";
import { eq, desc } from "drizzle-orm";
import { db } from "../db";
import * as schema from "../db/schema";
import { createModel } from "./models";
import { readMemory, listMemories } from "../memory";
import type { ProviderConfig } from "../llm/types";

const BRIEF_PROMPT = `You are ANIMA, a calm and thoughtful personal AI companion. Generate a brief daily greeting for the user based on the context provided.

Rules:
- Keep it to 2-4 short sentences maximum
- Be warm but restrained — no excessive enthusiasm
- Reference specific things from the context if relevant (current focus, open tasks, recent activity)
- If there's nothing notable, a simple quiet greeting is fine
- You may use a Japanese greeting if it feels natural
- Do NOT list out all the context — just weave in what matters
- Do NOT use bullet points or markdown formatting
- Sound like a person, not a report`;

interface BriefContext {
  currentFocus: string | null;
  openTasks: string[];
  recentTopics: string[];
  daysSinceLastChat: number | null;
  factsSnippet: string | null;
}

async function gatherBriefContext(userId: number): Promise<BriefContext> {
  // Current focus
  let currentFocus: string | null = null;
  try {
    const focus = await readMemory("user", userId, "current-focus");
    const line = focus.content
      .split("\n")
      .find((l) => l.trim().match(/^- \[[ xX]\]\s+/));
    if (line) {
      currentFocus = line.replace(/^- \[[ xX]\]\s+/, "").trim();
    }
  } catch {
    // No focus set
  }

  // Open tasks from goals
  const openTasks: string[] = [];
  try {
    const goals = await readMemory("user", userId, "goals");
    for (const line of goals.content.split("\n")) {
      const match = line.trim().match(/^- \[ \]\s+(.+)$/);
      if (match) openTasks.push(match[1].trim());
    }
  } catch {
    // No goals file
  }

  // Recent chat topics (last 6 user messages)
  const recentTopics: string[] = [];
  try {
    const rows = await db
      .select({ content: schema.messages.content })
      .from(schema.messages)
      .where(eq(schema.messages.userId, userId))
      .orderBy(desc(schema.messages.id))
      .limit(12);

    const userMsgs = rows
      .reverse()
      .filter((r) => true) // all messages for topic extraction
      .slice(-6);

    for (const msg of userMsgs) {
      if (msg.content.length > 10 && msg.content.length < 200) {
        recentTopics.push(msg.content.slice(0, 100));
      }
    }
  } catch {
    // No history
  }

  // Days since last chat
  let daysSinceLastChat: number | null = null;
  try {
    const [lastMsg] = await db
      .select({ createdAt: schema.messages.createdAt })
      .from(schema.messages)
      .where(eq(schema.messages.userId, userId))
      .orderBy(desc(schema.messages.id))
      .limit(1);

    if (lastMsg?.createdAt) {
      const lastDate = new Date(lastMsg.createdAt);
      const now = new Date();
      daysSinceLastChat = Math.floor(
        (now.getTime() - lastDate.getTime()) / (1000 * 60 * 60 * 24),
      );
    }
  } catch {
    // No messages
  }

  // Facts snippet
  let factsSnippet: string | null = null;
  try {
    const facts = await readMemory("user", userId, "facts");
    factsSnippet = facts.content.trim().slice(0, 300);
  } catch {
    // No facts
  }

  return { currentFocus, openTasks, recentTopics, daysSinceLastChat, factsSnippet };
}

export interface DailyBrief {
  message: string;
  context: {
    currentFocus: string | null;
    openTaskCount: number;
    daysSinceLastChat: number | null;
  };
}

// --- Cache: one brief per user per day ---
const briefCache = new Map<string, { brief: DailyBrief; timestamp: number }>();

function cacheKey(userId: number): string {
  const date = new Date().toISOString().slice(0, 10);
  return `${userId}:${date}`;
}

function getCachedBrief(userId: number): DailyBrief | null {
  const entry = briefCache.get(cacheKey(userId));
  if (!entry) return null;

  // Expire after 4 hours so context shifts (new tasks, focus changes) get picked up
  if (Date.now() - entry.timestamp > 4 * 60 * 60 * 1000) {
    briefCache.delete(cacheKey(userId));
    return null;
  }

  return entry.brief;
}

function setCachedBrief(userId: number, brief: DailyBrief): void {
  briefCache.set(cacheKey(userId), { brief, timestamp: Date.now() });
}

export async function generateBrief(userId: number): Promise<DailyBrief> {
  // Return cached brief if available
  const cached = getCachedBrief(userId);
  if (cached) return cached;

  const ctx = await gatherBriefContext(userId);

  // Build context string for the LLM
  const contextParts: string[] = [];

  if (ctx.factsSnippet) {
    contextParts.push(`User facts:\n${ctx.factsSnippet}`);
  }
  if (ctx.currentFocus) {
    contextParts.push(`Current focus: ${ctx.currentFocus}`);
  }
  if (ctx.openTasks.length > 0) {
    contextParts.push(`Open tasks (${ctx.openTasks.length}): ${ctx.openTasks.slice(0, 5).join(", ")}`);
  }
  if (ctx.recentTopics.length > 0) {
    contextParts.push(`Recent conversation topics:\n${ctx.recentTopics.join("\n")}`);
  }
  if (ctx.daysSinceLastChat !== null) {
    if (ctx.daysSinceLastChat === 0) {
      contextParts.push("Last chat: today");
    } else if (ctx.daysSinceLastChat === 1) {
      contextParts.push("Last chat: yesterday");
    } else {
      contextParts.push(`Last chat: ${ctx.daysSinceLastChat} days ago`);
    }
  }

  if (contextParts.length === 0) {
    // No context at all — return a static greeting
    return {
      message: "How was today?",
      context: {
        currentFocus: null,
        openTaskCount: 0,
        daysSinceLastChat: null,
      },
    };
  }

  // Get user's model config
  const [cfg] = await db
    .select()
    .from(schema.agentConfig)
    .where(eq(schema.agentConfig.userId, userId));

  const config: ProviderConfig = cfg
    ? {
        provider: cfg.provider as any,
        model: cfg.model,
        apiKey: cfg.apiKey || undefined,
        ollamaUrl: cfg.ollamaUrl || undefined,
      }
    : {
        provider: "ollama",
        model: "qwen3:14b",
        ollamaUrl: "http://localhost:11434",
      };

  const model = createModel(config);

  try {
    const result = await model.invoke([
      new SystemMessage(BRIEF_PROMPT),
      new HumanMessage(contextParts.join("\n\n")),
    ]);

    const message =
      typeof result.content === "string"
        ? result.content.trim()
        : "Something on your mind?";

    const brief: DailyBrief = {
      message,
      context: {
        currentFocus: ctx.currentFocus,
        openTaskCount: ctx.openTasks.length,
        daysSinceLastChat: ctx.daysSinceLastChat,
      },
    };

    setCachedBrief(userId, brief);
    return brief;
  } catch (err) {
    console.error("[brief] Generation failed:", (err as Error).message);

    // Fallback static brief — still cache it to avoid hammering a broken LLM
    const fallback: DailyBrief = {
      message: ctx.currentFocus
        ? `Still working on "${ctx.currentFocus}"?`
        : "How was today?",
      context: {
        currentFocus: ctx.currentFocus,
        openTaskCount: ctx.openTasks.length,
        daysSinceLastChat: ctx.daysSinceLastChat,
      },
    };

    setCachedBrief(userId, fallback);
    return fallback;
  }
}
