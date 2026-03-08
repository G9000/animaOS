// Nudge system — checks for conditions worth surfacing to the user.
// Returns structured nudges, not LLM-generated text (keeps it fast and deterministic).

import { eq, desc, and } from "drizzle-orm";
import { db } from "../db";
import * as schema from "../db/schema";
import { readMemory, listMemories } from "../memory";
import { isTaskOverdue } from "../lib/task-date";
import { maybeDecryptForUser } from "../lib/data-crypto";

export interface Nudge {
  type: "stale_focus" | "overdue_tasks" | "journal_gap" | "long_absence";
  message: string;
  priority: number; // 1 = high, 2 = medium, 3 = low
}

async function checkStaleFocus(userId: number): Promise<Nudge | null> {
  try {
    const focus = await readMemory("user", userId, "current-focus");
    const updated = focus.meta.updated;
    if (!updated) return null;

    const daysSinceUpdate = Math.floor(
      (Date.now() - new Date(String(updated)).getTime()) / (1000 * 60 * 60 * 24),
    );

    if (daysSinceUpdate >= 3) {
      const focusLine = focus.content
        .split("\n")
        .find((l) => l.trim().match(/^- \[[ ]\]\s+/));
      const focusText = focusLine
        ?.replace(/^- \[ \]\s+/, "")
        .trim();

      return {
        type: "stale_focus",
        message: focusText
          ? `Your focus "${focusText}" hasn't moved in ${daysSinceUpdate} days.`
          : `Your current focus hasn't been updated in ${daysSinceUpdate} days.`,
        priority: 2,
      };
    }
  } catch {
    // No focus file
  }

  return null;
}

async function checkOverdueTasks(userId: number): Promise<Nudge | null> {
  try {
    const openTasks = await db
      .select({
        id: schema.tasks.id,
        text: schema.tasks.text,
        dueDate: schema.tasks.dueDate,
      })
      .from(schema.tasks)
      .where(and(eq(schema.tasks.userId, userId), eq(schema.tasks.done, false)));

    const overdue = openTasks.filter((t) => isTaskOverdue(t.dueDate));

    if (overdue.length > 0) {
      const names = overdue
        .slice(0, 3)
        .map((t) => `"${maybeDecryptForUser(userId, t.text)}"`)
        .join(", ");
      const extra = overdue.length > 3 ? ` and ${overdue.length - 3} more` : "";
      return {
        type: "overdue_tasks",
        message: `You have ${overdue.length} overdue task${overdue.length > 1 ? "s" : ""}: ${names}${extra}. Want to reschedule or mark them done?`,
        priority: 1,
      };
    }

    // Fallback: too many open tasks
    if (openTasks.length >= 5) {
      return {
        type: "overdue_tasks",
        message: `You have ${openTasks.length} open tasks. Maybe time to close some or reprioritize.`,
        priority: 3,
      };
    }
  } catch {
    // Tasks table might not exist yet
  }

  return null;
}

async function checkJournalGap(userId: number): Promise<Nudge | null> {
  try {
    const entries = await listMemories("journal", userId);
    if (entries.length === 0) {
      return {
        type: "journal_gap",
        message: "You haven't journaled yet. Even a short entry helps track your days.",
        priority: 3,
      };
    }

    // Find most recent journal date from filenames (YYYY-MM-DD.md)
    const dates = entries
      .map((e) => {
        const match = e.path.match(/(\d{4}-\d{2}-\d{2})\.md$/);
        return match ? new Date(match[1]) : null;
      })
      .filter(Boolean) as Date[];

    if (dates.length === 0) return null;

    const latest = Math.max(...dates.map((d) => d.getTime()));
    const daysSince = Math.floor(
      (Date.now() - latest) / (1000 * 60 * 60 * 24),
    );

    if (daysSince >= 3) {
      return {
        type: "journal_gap",
        message: `No journal entry in ${daysSince} days. A quick note goes a long way.`,
        priority: 3,
      };
    }
  } catch {
    // Journal section doesn't exist
  }

  return null;
}

async function checkLongAbsence(userId: number): Promise<Nudge | null> {
  try {
    const [lastMsg] = await db
      .select({ createdAt: schema.messages.createdAt })
      .from(schema.messages)
      .where(eq(schema.messages.userId, userId))
      .orderBy(desc(schema.messages.id))
      .limit(1);

    if (!lastMsg?.createdAt) return null;

    const daysSince = Math.floor(
      (Date.now() - new Date(lastMsg.createdAt).getTime()) / (1000 * 60 * 60 * 24),
    );

    if (daysSince >= 7) {
      return {
        type: "long_absence",
        message: `It's been ${daysSince} days since we last talked.`,
        priority: 1,
      };
    }
  } catch {
    // No messages
  }

  return null;
}

/**
 * Check all nudge conditions and return applicable ones, sorted by priority.
 */
export async function checkNudges(userId: number): Promise<Nudge[]> {
  const checks = await Promise.all([
    checkLongAbsence(userId),
    checkStaleFocus(userId),
    checkOverdueTasks(userId),
    checkJournalGap(userId),
  ]);

  return checks
    .filter((n): n is Nudge => n !== null)
    .sort((a, b) => a.priority - b.priority);
}
