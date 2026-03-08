// Memory context loader — OpenClaw-style conversation-aware retrieval.
//
// Instead of fixed-section context stuffing, this module:
// 1. Loads essential profile data (always needed)
// 2. Reads recent daily logs (today + yesterday)
// 3. Uses semantic search to retrieve the most relevant memories for the current conversation
// 4. Injects tasks from DB
//
// This ensures the agent always has the right context for the current conversation,
// rather than a generic dump of sections.

import { readMemory, readRecentDailyLogs, type MemorySection } from "../memory";
import { retrieveContextMemories } from "../memory/manager";
import { eq } from "drizzle-orm";
import { db } from "../db";
import * as schema from "../db/schema";
import { maybeDecryptForUser } from "../lib/data-crypto";

// Increased from 3000 — semantic retrieval is much more selective
const MAX_CONTEXT_CHARS = 6000;

// Essential files always loaded (like OpenClaw's SOUL.md + USER.md bootstrap)
const ESSENTIAL_FILES: {
  section: MemorySection;
  filename: string;
  label: string;
}[] = [
  { section: "user", filename: "facts", label: "Facts" },
  { section: "user", filename: "preferences", label: "Preferences" },
  { section: "user", filename: "current-focus", label: "Current Focus" },
];

/**
 * Load essential profile data — always included regardless of conversation topic.
 * These are the "bootstrap files" equivalent from OpenClaw.
 */
async function loadEssentialContext(userId: number): Promise<string> {
  const lines: string[] = [];

  for (const { section, filename, label } of ESSENTIAL_FILES) {
    try {
      const file = await readMemory(section, userId, filename);
      const body = file.content.trim();
      if (body) {
        lines.push(`### ${label}\n${body}`);
      }
    } catch {
      // File doesn't exist yet — skip
    }
  }

  return lines.length > 0 ? `## About the user\n${lines.join("\n\n")}` : "";
}

/**
 * Load recent daily logs — like OpenClaw's "read today + yesterday at session start".
 */
async function loadDailyContext(userId: number): Promise<string> {
  const logs = await readRecentDailyLogs(userId, 2);
  if (logs.length === 0) return "";

  const parts = logs.map((log) => `### ${log.date}\n${log.content}`);
  return `## Recent Context\n${parts.join("\n\n")}`;
}

/**
 * Load tasks from DB.
 */
async function loadTaskContext(userId: number): Promise<string> {
  try {
    const taskRows = await db
      .select()
      .from(schema.tasks)
      .where(eq(schema.tasks.userId, userId));

    if (taskRows.length === 0) return "";

    const openTasks = taskRows.filter((t) => !t.done);
    const doneTasks = taskRows.filter((t) => t.done);
    const taskLines: string[] = [];

    for (const t of openTasks) {
      const extra = t.dueDate ? ` (due: ${t.dueDate})` : "";
      taskLines.push(`- [ ] ${maybeDecryptForUser(userId, t.text)}${extra}`);
    }
    for (const t of doneTasks.slice(-3)) {
      taskLines.push(`- [x] ${maybeDecryptForUser(userId, t.text)}`);
    }

    return taskLines.length > 0 ? `## Tasks\n${taskLines.join("\n")}` : "";
  } catch {
    return "";
  }
}

/**
 * Load memory context for injection into the system prompt.
 *
 * New approach (OpenClaw-style):
 * 1. Essential profile data (always loaded)
 * 2. Recent daily logs (today + yesterday)
 * 3. Tasks from DB
 * 4. Conversation-aware semantic retrieval (when conversationHint is provided)
 *
 * The `conversationHint` parameter lets us retrieve memories relevant to
 * what the user is currently talking about, instead of a fixed dump.
 */
export async function loadMemoryContext(
  userId: number,
  conversationHint?: string,
): Promise<string> {
  const parts: string[] = [];
  let totalChars = 0;

  // 1. Essential profile data (always loaded)
  const essential = await loadEssentialContext(userId);
  if (essential) {
    parts.push(essential);
    totalChars += essential.length;
  }

  // 2. Recent daily logs
  const daily = await loadDailyContext(userId);
  if (daily && totalChars + daily.length < MAX_CONTEXT_CHARS) {
    parts.push(daily);
    totalChars += daily.length;
  }

  // 3. Tasks
  const tasks = await loadTaskContext(userId);
  if (tasks && totalChars + tasks.length < MAX_CONTEXT_CHARS) {
    parts.push(tasks);
    totalChars += tasks.length;
  }

  // 4. Conversation-aware semantic retrieval
  if (conversationHint) {
    try {
      const remainingTokens = Math.floor((MAX_CONTEXT_CHARS - totalChars) / 4);
      if (remainingTokens > 200) {
        const semantic = await retrieveContextMemories(
          userId,
          conversationHint,
          remainingTokens,
        );
        if (semantic) {
          // Strip the header since we'll add our own
          const body = semantic.replace(/^#[^\n]*\n[^\n]*\n[^\n]*\n\n?/, "");
          if (body.trim()) {
            parts.push(`## Relevant Memories\n${body.trim()}`);
          }
        }
      }
    } catch (err) {
      console.warn(
        "[context] Semantic retrieval failed:",
        (err as Error).message,
      );
    }
  }

  if (parts.length === 0) return "";

  return `# What you know about this user\nUse this context naturally in conversation. Do not repeat it back verbatim.\n\n${parts.join("\n\n")}`;
}
