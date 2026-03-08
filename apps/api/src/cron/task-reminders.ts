// Task reminder system — BullMQ-based delayed job queue.
//
// Two jobs per task with a dueDate:
//   "pre"      → fires 15 min before due
//   "followup" → fires 5 min after due, asks if user completed it
//
// Jobs are scheduled with exact delays via BullMQ's delayed job feature.
// Redis handles persistence, retry, and deduplication (jobId = task-{id}-{phase}).

import { Queue, Worker, type Job } from "bullmq";
import { eq } from "drizzle-orm";
import { db } from "../db";
import * as schema from "../db/schema";
import { redisConnection } from "../lib/redis";
import { parseDueDateDeadline } from "../lib/task-date";
import { maybeDecryptForUser, maybeEncryptForUser } from "../lib/data-crypto";

const QUEUE_NAME = "task-reminders";
const FOLLOWUP_WINDOW_MINUTES = 24 * 60;
const MAX_ATTEMPTS = 5;

// Priority-based reminder timing (minutes)
// priority 0 = normal, 1 = high, 2 = urgent
const PRE_REMINDER_MINUTES: Record<number, number[]> = {
  0: [15], // normal: 15 min before
  1: [60, 15], // high: 1 hour + 15 min before
  2: [120, 30, 5], // urgent: 2 hours + 30 min + 5 min before
};
const FOLLOWUP_AFTER_MINUTES = 5;

// --- Types ---

interface ReminderJobData {
  taskId: number;
  userId: number;
  phase: string; // "pre-0", "pre-1", "pre-2", or "followup"
}

// --- Queue ---

const reminderQueue = new Queue<ReminderJobData>(QUEUE_NAME, {
  connection: redisConnection,
  defaultJobOptions: {
    attempts: MAX_ATTEMPTS,
    backoff: { type: "exponential", delay: 60_000 }, // 1m, 2m, 4m, 8m, 16m
    removeOnComplete: { age: 7 * 24 * 3600 }, // keep 7 days
    removeOnFail: { age: 14 * 24 * 3600 }, // keep 14 days
  },
});

// --- Helpers ---

function jobId(taskId: number, phase: string): string {
  return `task-${taskId}-${phase}`;
}

async function sendTelegramMessage(
  token: string,
  chatId: number,
  text: string,
): Promise<void> {
  const res = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Telegram send failed: ${res.status} ${body}`);
  }
}

async function notifyUser(userId: number, message: string): Promise<void> {
  // In-app message FIRST — this must succeed regardless of Telegram
  await db.insert(schema.messages).values({
    userId,
    role: "system",
    content: maybeEncryptForUser(userId, message),
  });

  // Telegram is best-effort — never block in-app delivery
  const token = process.env.TELEGRAM_BOT_TOKEN;
  if (token) {
    try {
      const [link] = await db
        .select()
        .from(schema.telegramLinks)
        .where(eq(schema.telegramLinks.userId, userId))
        .limit(1);

      if (link) {
        await sendTelegramMessage(token, link.chatId, message);
      }
    } catch (err) {
      console.error(
        `[task-reminder] Telegram notify failed for user ${userId}:`,
        (err as Error).message,
      );
    }
  }
}

// --- Job processor ---

async function processReminder(job: Job<ReminderJobData>): Promise<void> {
  const { taskId, userId, phase } = job.data;

  // Fetch fresh task state
  const [task] = await db
    .select()
    .from(schema.tasks)
    .where(eq(schema.tasks.id, taskId))
    .limit(1);

  if (!task) throw new Error(`Task ${taskId} not found`);
  if (task.done) return; // task completed — skip silently
  if (!task.dueDate) return;

  const due = parseDueDateDeadline(task.dueDate);
  if (!due) throw new Error(`Invalid due date: ${task.dueDate}`);

  const nowMs = Date.now();
  const dueMs = due.getTime();
  const minutesUntilDue = (dueMs - nowMs) / 60_000;

  const dueTime = due.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
  });

  const taskText = maybeDecryptForUser(userId, task.text);

  if (phase.startsWith("pre")) {
    if (minutesUntilDue <= 0) {
      console.log(
        `[task-reminder] Pre-reminder window missed for task ${taskId} (${phase})`,
      );
      return;
    }
    const urgencyLabel =
      (task.priority ?? 0) >= 2
        ? "🚨"
        : (task.priority ?? 0) >= 1
          ? "⚠️"
          : "⏰";
    await notifyUser(
      userId,
      `${urgencyLabel} Heads up — "${taskText}" is due at ${dueTime} (in ~${Math.round(minutesUntilDue)} min)`,
    );
    console.log(
      `[task-reminder] Pre-reminder (${phase}) sent for task ${taskId} (user ${userId})`,
    );
    return;
  }

  // followup phase
  const minsAgo = Math.round(Math.abs(minutesUntilDue));
  if (minsAgo > FOLLOWUP_WINDOW_MINUTES) {
    console.log(
      `[task-reminder] Follow-up window expired for task ${taskId} (${minsAgo} min ago)`,
    );
    return;
  }

  await notifyUser(
    userId,
    `👋 Did you get to "${taskText}"? It was due at ${dueTime} (${minsAgo} min ago). Let me know if it's done or if you need to reschedule.`,
  );
  console.log(
    `[task-reminder] Follow-up sent for task ${taskId} (user ${userId})`,
  );
}

// --- Public API: schedule/cancel reminders ---

export async function syncReminderJobsForTask(
  task: typeof schema.tasks.$inferSelect,
): Promise<void> {
  if (!task.dueDate || task.done) {
    await cancelReminderJobsForTask(task.id);
    return;
  }

  const due = parseDueDateDeadline(task.dueDate);
  if (!due) {
    await cancelReminderJobsForTask(task.id);
    return;
  }

  const now = Date.now();
  const dueMs = due.getTime();
  const priority = task.priority ?? 0;

  // --- Schedule pre-reminders based on priority ---
  const preOffsets = PRE_REMINDER_MINUTES[priority] ?? PRE_REMINDER_MINUTES[0];

  // First, clean up any old pre-reminder jobs for this task (handles priority changes)
  for (let i = 0; i < 5; i++) {
    const oldId = jobId(task.id, `pre-${i}`);
    try {
      const existing = await reminderQueue.getJob(oldId);
      if (existing) await existing.remove();
    } catch {
      /* job doesn't exist */
    }
  }
  // Also clean legacy "pre" jobId from before priority-based reminders
  try {
    const legacy = await reminderQueue.getJob(jobId(task.id, "pre"));
    if (legacy) await legacy.remove();
  } catch {
    /* fine */
  }

  for (let i = 0; i < preOffsets.length; i++) {
    const offsetMs = -preOffsets[i] * 60_000;
    const runAtMs = dueMs + offsetMs;
    const delay = Math.max(runAtMs - now, 0);
    const phase = `pre-${i}`;
    const id = jobId(task.id, phase);

    // Skip if due time already passed
    if (dueMs <= now) continue;
    // Skip if this reminder time already passed
    if (runAtMs <= now) continue;

    await reminderQueue.add(
      "reminder",
      {
        taskId: task.id,
        userId: task.userId,
        phase,
      },
      {
        jobId: id,
        delay,
      },
    );

    console.log(
      `[task-reminder] Scheduled ${phase} (${preOffsets[i]}min before) for task ${task.id} (delay: ${Math.round(delay / 1000)}s)`,
    );
  }

  // --- Schedule followup ---
  const followupPhase = "followup";
  const followupId = jobId(task.id, followupPhase);
  try {
    const existing = await reminderQueue.getJob(followupId);
    if (existing) await existing.remove();
  } catch {
    /* fine */
  }

  if (!(now - dueMs > FOLLOWUP_WINDOW_MINUTES * 60_000)) {
    const followupDelay = Math.max(
      dueMs + FOLLOWUP_AFTER_MINUTES * 60_000 - now,
      0,
    );
    await reminderQueue.add(
      "reminder",
      {
        taskId: task.id,
        userId: task.userId,
        phase: followupPhase,
      },
      {
        jobId: followupId,
        delay: followupDelay,
      },
    );

    console.log(
      `[task-reminder] Scheduled followup for task ${task.id} (delay: ${Math.round(followupDelay / 1000)}s)`,
    );
  }
}

export async function cancelReminderJobsForTask(
  taskId: number,
  reason?: string,
): Promise<void> {
  // Cancel all possible pre-reminder slots + followup + legacy "pre"
  const phases = [
    "pre",
    "followup",
    "pre-0",
    "pre-1",
    "pre-2",
    "pre-3",
    "pre-4",
  ];
  for (const phase of phases) {
    try {
      const existing = await reminderQueue.getJob(jobId(taskId, phase));
      if (existing) {
        await existing.remove();
        console.log(
          `[task-reminder] Cancelled ${phase} for task ${taskId}${reason ? ` (${reason})` : ""}`,
        );
      }
    } catch {
      // job doesn't exist or already completed
    }
  }
}

export async function syncReminderJobsForTaskId(taskId: number): Promise<void> {
  const [task] = await db
    .select()
    .from(schema.tasks)
    .where(eq(schema.tasks.id, taskId))
    .limit(1);

  if (!task) {
    await cancelReminderJobsForTask(taskId);
    return;
  }

  await syncReminderJobsForTask(task);
}

// --- Worker lifecycle ---

let worker: Worker<ReminderJobData> | null = null;

export function startTaskReminderCron(): void {
  if (worker) return;

  worker = new Worker<ReminderJobData>(QUEUE_NAME, processReminder, {
    connection: redisConnection,
    concurrency: 5,
  });

  worker.on("completed", (job) => {
    console.log(`[task-reminder] Job ${job.id} completed`);
  });

  worker.on("failed", (job, err) => {
    console.error(`[task-reminder] Job ${job?.id} failed: ${err.message}`);
  });

  console.log("[task-reminder] BullMQ worker started");

  // Reconcile: schedule jobs for any tasks with due dates that don't have jobs yet
  reconcileReminderJobs().catch((err) =>
    console.error(
      "[task-reminder] Reconciliation error:",
      (err as Error).message,
    ),
  );
}

export async function stopTaskReminderCron(): Promise<void> {
  if (!worker) return;
  await worker.close();
  await reminderQueue.close();
  worker = null;
  console.log("[task-reminder] BullMQ worker stopped");
}

async function reconcileReminderJobs(): Promise<void> {
  const tasks = await db
    .select()
    .from(schema.tasks)
    .where(eq(schema.tasks.done, false));

  let scheduled = 0;
  for (const task of tasks) {
    if (!task.dueDate) continue;
    await syncReminderJobsForTask(task);
    scheduled++;
  }

  if (scheduled > 0) {
    console.log(`[task-reminder] Reconciled ${scheduled} task(s)`);
  }
}
