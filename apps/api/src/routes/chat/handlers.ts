// Chat route handlers

import type { Context } from "hono";
import { eq, desc } from "drizzle-orm";
import { db } from "../../db";
import * as schema from "../../db/schema";
import { resetAgentThread } from "../../agent";
import { generateBrief } from "../../agent/brief";
import { checkNudges } from "../../agent/nudge";
import { consolidateMemories } from "../../agent/consolidate";
import { readMemory, listMemories, listAllMemories } from "../../memory";
import { handleChannelMessage, handleChannelStreamMessage } from "../../channels";
import { requireUnlockedUser } from "../../lib/require-unlock";
import { maybeDecryptForUser } from "../../lib/data-crypto";

// POST /chat
export async function sendMessage(c: Context) {
  const { message, userId, stream } = c.req.valid("json" as never);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  if (!stream) {
    try {
      const result = await handleChannelMessage({
        channel: "chat",
        userId,
        text: message,
      });
      return c.json({
        response: result.text,
        model: result.model || "unknown",
        provider: result.provider || "unknown",
        toolsUsed: result.toolsUsed || [],
      });
    } catch (err: any) {
      return c.json({ error: err.message }, 500);
    }
  }

  // SSE streaming
  return new Response(
    new ReadableStream({
      async start(controller) {
        const encoder = new TextEncoder();
        const send = (event: string, data: unknown) => {
          controller.enqueue(
            encoder.encode(
              `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
            ),
          );
        };

        try {
          for await (const chunk of handleChannelStreamMessage({
            channel: "chat",
            userId,
            text: message,
          })) {
            send("chunk", { content: chunk });
          }
          send("done", { status: "complete" });
        } catch (err: any) {
          send("error", { error: err.message });
        } finally {
          controller.close();
        }
      },
    }),
    {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    },
  );
}

// GET /chat/history
export async function getHistory(c: Context) {
  const { userId, limit } = c.req.valid("query" as never);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  const messages = await db
    .select()
    .from(schema.messages)
    .where(eq(schema.messages.userId, userId))
    .orderBy(desc(schema.messages.id))
    .limit(limit);

  return c.json(
    messages.reverse().map((msg) => ({
      ...msg,
      content: maybeDecryptForUser(userId, msg.content),
    })),
  );
}

// DELETE /chat/history
export async function clearHistory(c: Context) {
  const { userId } = c.req.valid("json" as never);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  await db.delete(schema.messages).where(eq(schema.messages.userId, userId));
  await resetAgentThread(userId).catch(() => {
    // Ignore reset failures so chat history clear still succeeds.
  });
  return c.json({ status: "cleared" });
}

// GET /chat/brief
export async function getBrief(c: Context) {
  const { userId } = c.req.valid("query" as never);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  try {
    const brief = await generateBrief(userId);
    return c.json(brief);
  } catch (err: any) {
    return c.json({ error: err.message }, 500);
  }
}

// GET /chat/nudges
export async function getNudges(c: Context) {
  const { userId } = c.req.valid("query" as never);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  try {
    const nudges = await checkNudges(userId);
    return c.json({ nudges });
  } catch (err: any) {
    return c.json({ nudges: [] });
  }
}

// GET /chat/home
export async function getHome(c: Context) {
  const { userId } = c.req.valid("query" as never);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  const [focusResult, taskRows, journalEntries, allMemories, messageCount] =
    await Promise.all([
      readMemory("user", userId, "current-focus").catch(() => null),
      db
        .select()
        .from(schema.tasks)
        .where(eq(schema.tasks.userId, userId))
        .catch(() => []),
      listMemories("journal", userId).catch(() => []),
      listAllMemories(userId).catch(() => []),
      db
        .select({ id: schema.messages.id })
        .from(schema.messages)
        .where(eq(schema.messages.userId, userId))
        .then((rows) => rows.length)
        .catch(() => 0),
    ]);

  // Parse current focus
  let currentFocus: string | null = null;
  if (focusResult) {
    const line = focusResult.content
      .split("\n")
      .find((l) => l.trim().match(/^- \[[ ]\]\s+/));
    if (line) currentFocus = line.replace(/^- \[ \]\s+/, "").trim();
  }

  // Tasks from DB
  const tasks = (taskRows as any[]).map((t) => ({
    id: t.id,
    text: maybeDecryptForUser(userId, t.text),
    done: t.done,
    priority: t.priority,
    dueDate: t.dueDate,
  }));

  // Journal streak — count consecutive recent days with entries
  const journalDateSet = new Set(
    journalEntries
      .map((e: any) => {
        const m = e.path.match(/(\d{4}-\d{2}-\d{2})\.md$/);
        return m ? m[1] : null;
      })
      .filter(Boolean),
  );
  let journalStreak = 0;
  const today = new Date();
  for (let i = 0; i < 365; i++) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    if (journalDateSet.has(key)) {
      journalStreak++;
    } else if (i > 0) {
      break;
    }
    // skip today if no entry yet (don't break streak)
  }

  return c.json({
    currentFocus,
    tasks,
    journalStreak,
    journalTotal: journalEntries.length,
    memoryCount: allMemories.length,
    messageCount,
  });
}

// POST /chat/consolidate
export async function consolidate(c: Context) {
  const { userId } = c.req.valid("json" as never);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  try {
    const result = await consolidateMemories(userId);
    return c.json(result);
  } catch (err: any) {
    return c.json({ error: err.message }, 500);
  }
}
