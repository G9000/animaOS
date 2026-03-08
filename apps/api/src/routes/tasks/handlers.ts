// Task route handlers

import type { Context } from "hono";
import { eq, asc } from "drizzle-orm";
import { db } from "../../db";
import * as schema from "../../db/schema";
import {
  cancelReminderJobsForTask,
  syncReminderJobsForTask,
} from "../../cron/task-reminders";
import { resolveDueDate } from "../../lib/task-date";
import { requireUnlockedUser } from "../../lib/require-unlock";
import { maybeDecryptForUser, maybeEncryptForUser } from "../../lib/data-crypto";

// GET /tasks
export async function listTasks(c: Context) {
  const { userId } = c.req.valid("query" as never);
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  const rows = await db
    .select()
    .from(schema.tasks)
    .where(eq(schema.tasks.userId, userId))
    .orderBy(asc(schema.tasks.done), asc(schema.tasks.createdAt));
  return c.json(rows.map((row) => ({ ...row, text: maybeDecryptForUser(userId, row.text) })));
}

// POST /tasks
export async function createTask(c: Context) {
  const { userId, text, priority, dueDate, dueDateRaw } = c.req.valid(
    "json" as never,
  );
  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  // Resolution: dueDateRaw (chrono NLP) → dueDate (ISO or NLP) → parse from task text
  const resolvedDueDate =
    resolveDueDate(dueDateRaw) ??
    resolveDueDate(dueDate) ??
    resolveDueDate(text);

  const [task] = await db
    .insert(schema.tasks)
    .values({
      userId,
      text: maybeEncryptForUser(userId, text),
      priority: priority ?? 0,
      dueDate: resolvedDueDate,
    })
    .returning();

  await syncReminderJobsForTask(task);

  return c.json({ ...task, text }, 201);
}

// PUT /tasks/:id
export async function updateTask(c: Context) {
  const id = Number(c.req.param("id"));
  const existingTask = await db
    .select()
    .from(schema.tasks)
    .where(eq(schema.tasks.id, id))
    .then((rows) => rows[0]);
  if (!existingTask) return c.json({ error: "Task not found" }, 404);

  const auth = requireUnlockedUser(c, existingTask.userId);
  if (!auth.ok) return auth.response;

  const updates = c.req.valid("json" as never) as {
    text?: string;
    done?: boolean;
    priority?: number;
    dueDate?: string | null;
  };

  const data: Record<string, unknown> = {
    updatedAt: new Date().toISOString(),
  };
  if (updates.text !== undefined) {
    data.text = maybeEncryptForUser(existingTask.userId, updates.text);
  }
  if (updates.priority !== undefined) data.priority = updates.priority;
  if (updates.dueDate !== undefined) {
    data.dueDate =
      updates.dueDate === null ? null : resolveDueDate(updates.dueDate);
  }
  if (updates.done !== undefined) {
    data.done = updates.done;
    data.completedAt = updates.done ? new Date().toISOString() : null;
  }

  const [task] = await db
    .update(schema.tasks)
    .set(data)
    .where(eq(schema.tasks.id, id))
    .returning();

  if (!task) return c.json({ error: "Task not found" }, 404);

  await syncReminderJobsForTask(task);

  return c.json({
    ...task,
    text:
      updates.text !== undefined
        ? updates.text
        : maybeDecryptForUser(existingTask.userId, task.text),
  });
}

// DELETE /tasks/:id
export async function deleteTask(c: Context) {
  const id = Number(c.req.param("id"));
  const existingTask = await db
    .select()
    .from(schema.tasks)
    .where(eq(schema.tasks.id, id))
    .then((rows) => rows[0]);
  if (!existingTask) return c.json({ error: "Task not found" }, 404);

  const auth = requireUnlockedUser(c, existingTask.userId);
  if (!auth.ok) return auth.response;

  const [deleted] = await db
    .delete(schema.tasks)
    .where(eq(schema.tasks.id, id))
    .returning();

  if (!deleted) return c.json({ error: "Task not found" }, 404);
  await cancelReminderJobsForTask(id, "Task deleted");
  return c.json({ status: "deleted" });
}
