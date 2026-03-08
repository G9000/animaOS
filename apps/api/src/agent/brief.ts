// Daily brief generator — produces a short briefing from memory context.

import { SystemMessage, HumanMessage } from "@langchain/core/messages";
import { and, eq, desc } from "drizzle-orm";
import { db } from "../db";
import * as schema from "../db/schema";
import { createModel } from "./models";
import { readMemory } from "../memory";
import { getAgentConfig } from "./config";
import { getSoulPromptForUser, renderPromptTemplate } from "./prompt";
import { isTaskOpen } from "../lib/task-date";
import { maybeDecryptForUser } from "../lib/data-crypto";

async function buildBriefPrompt(userId: number): Promise<string> {
  const soulPrompt = await getSoulPromptForUser(userId);
  return renderPromptTemplate("brief-system", {
    soul_prompt: soulPrompt,
  });
}

interface BriefContext {
  currentFocus: string | null;
  openTasks: string[];
  recentTopics: string[];
  daysSinceLastChat: number | null;
  factsSnippet: string | null;
}

function toLocalDateKey(date: Date = new Date()): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function logBriefContextError(scope: string, err: unknown): void {
  const message = err instanceof Error ? err.message : String(err);
  console.debug(`[brief] Context load failed (${scope}): ${message}`);
}

async function loadCurrentFocus(userId: number): Promise<string | null> {
  try {
    const focus = await readMemory("user", userId, "current-focus");
    const line = focus.content
      .split("\n")
      .find((l) => l.trim().match(/^- \[[ xX]\]\s+/));
    if (!line) return null;
    return line.replace(/^- \[[ xX]\]\s+/, "").trim();
  } catch (err) {
    logBriefContextError("focus", err);
    return null;
  }
}

async function loadOpenTasks(userId: number): Promise<string[]> {
  try {
    const rows = await db
      .select({
        text: schema.tasks.text,
        done: schema.tasks.done,
        dueDate: schema.tasks.dueDate,
      })
      .from(schema.tasks)
      .where(eq(schema.tasks.userId, userId));

    return rows
      .filter((r) => isTaskOpen(r.done, r.dueDate))
      .map((r) => maybeDecryptForUser(userId, r.text));
  } catch (err) {
    logBriefContextError("tasks", err);
    return [];
  }
}

async function loadRecentTopics(userId: number): Promise<string[]> {
  try {
    const rows = await db
      .select({ content: schema.messages.content })
      .from(schema.messages)
      .where(
        and(
          eq(schema.messages.userId, userId),
          eq(schema.messages.role, "user"),
        ),
      )
      .orderBy(desc(schema.messages.id))
      .limit(12);

    return rows
      .reverse()
      .slice(-6)
      .map((r) => maybeDecryptForUser(userId, r.content))
      .filter((content) => content.length > 10 && content.length < 200)
      .map((content) => content.slice(0, 100));
  } catch (err) {
    logBriefContextError("recent-topics", err);
    return [];
  }
}

async function loadDaysSinceLastChat(userId: number): Promise<number | null> {
  try {
    const [lastMsg] = await db
      .select({ createdAt: schema.messages.createdAt })
      .from(schema.messages)
      .where(eq(schema.messages.userId, userId))
      .orderBy(desc(schema.messages.id))
      .limit(1);

    if (!lastMsg?.createdAt) return null;
    const lastDate = new Date(lastMsg.createdAt);
    const now = new Date();
    return Math.floor(
      (now.getTime() - lastDate.getTime()) / (1000 * 60 * 60 * 24),
    );
  } catch (err) {
    logBriefContextError("last-chat", err);
    return null;
  }
}

async function loadFactsSnippet(userId: number): Promise<string | null> {
  try {
    const facts = await readMemory("user", userId, "facts");
    return facts.content.trim().slice(0, 300);
  } catch (err) {
    logBriefContextError("facts", err);
    return null;
  }
}

async function gatherBriefContext(userId: number): Promise<BriefContext> {
  const [
    currentFocus,
    openTasks,
    recentTopics,
    daysSinceLastChat,
    factsSnippet,
  ] = await Promise.all([
    loadCurrentFocus(userId),
    loadOpenTasks(userId),
    loadRecentTopics(userId),
    loadDaysSinceLastChat(userId),
    loadFactsSnippet(userId),
  ]);

  return {
    currentFocus,
    openTasks,
    recentTopics,
    daysSinceLastChat,
    factsSnippet,
  };
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
  const date = toLocalDateKey();
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
    contextParts.push(
      `Open tasks (${ctx.openTasks.length}): ${ctx.openTasks.slice(0, 5).join(", ")}`,
    );
  }
  if (ctx.recentTopics.length > 0) {
    contextParts.push(
      `Recent conversation topics:\n${ctx.recentTopics.join("\n")}`,
    );
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

  const config = await getAgentConfig(userId);
  const model = createModel(config);

  try {
    const result = await model.invoke([
      new SystemMessage(await buildBriefPrompt(userId)),
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
