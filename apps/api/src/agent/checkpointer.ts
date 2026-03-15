import type { RunnableConfig } from "@langchain/core/runnables";
import {
  BaseCheckpointSaver,
  WRITES_IDX_MAP,
  getCheckpointId,
  type Checkpoint,
  type CheckpointListOptions,
  type CheckpointMetadata,
  type CheckpointTuple,
  type PendingWrite,
} from "@langchain/langgraph-checkpoint";
import { and, desc, eq } from "drizzle-orm";
import { db } from "../db";
import * as schema from "../db/schema";

const DEFAULT_CHECKPOINT_NS = "assistant";

function parseJson<T>(value: string): T {
  return JSON.parse(value) as T;
}

function stringifyJson(value: unknown): string {
  return JSON.stringify(value);
}

function getThreadId(config: RunnableConfig): string | undefined {
  const threadId = config.configurable?.thread_id;
  return typeof threadId === "string" ? threadId : undefined;
}

function getCheckpointNs(config: RunnableConfig): string {
  const checkpointNs = config.configurable?.checkpoint_ns;
  return typeof checkpointNs === "string" ? checkpointNs : "";
}

function getParentCheckpointId(config: RunnableConfig): string | null {
  return typeof config.configurable?.checkpoint_id === "string"
    ? config.configurable.checkpoint_id
    : null;
}

async function getPendingWriteRows(
  threadId: string,
  checkpointNs: string,
  checkpointId: string,
) {
  return db
    .select()
    .from(schema.langgraphWrites)
    .where(
      and(
        eq(schema.langgraphWrites.threadId, threadId),
        eq(schema.langgraphWrites.checkpointNs, checkpointNs),
        eq(schema.langgraphWrites.checkpointId, checkpointId),
      ),
    )
    .orderBy(schema.langgraphWrites.id);
}

function toCheckpointTuple(
  row: typeof schema.langgraphCheckpoints.$inferSelect,
  pendingWriteRows: typeof schema.langgraphWrites.$inferSelect[],
): CheckpointTuple {
  const tuple: CheckpointTuple = {
    config: {
      configurable: {
        thread_id: row.threadId,
        checkpoint_ns: row.checkpointNs,
        checkpoint_id: row.checkpointId,
      },
    },
    checkpoint: parseJson<Checkpoint>(row.checkpoint),
    metadata: parseJson<CheckpointMetadata>(row.metadata),
    pendingWrites: pendingWriteRows.map((w) => [
      w.taskId,
      w.channel,
      parseJson(w.value),
    ]),
  };

  if (row.parentCheckpointId) {
    tuple.parentConfig = {
      configurable: {
        thread_id: row.threadId,
        checkpoint_ns: row.checkpointNs,
        checkpoint_id: row.parentCheckpointId,
      },
    };
  }

  return tuple;
}

class SqliteCheckpointSaver extends BaseCheckpointSaver {
  async getTuple(config: RunnableConfig): Promise<CheckpointTuple | undefined> {
    const threadId = getThreadId(config);
    if (!threadId) return undefined;

    const checkpointNs = getCheckpointNs(config);
    const requestedCheckpointId = getCheckpointId(config);

    const [row] = requestedCheckpointId
      ? await db
          .select()
          .from(schema.langgraphCheckpoints)
          .where(
            and(
              eq(schema.langgraphCheckpoints.threadId, threadId),
              eq(schema.langgraphCheckpoints.checkpointNs, checkpointNs),
              eq(schema.langgraphCheckpoints.checkpointId, requestedCheckpointId),
            ),
          )
          .limit(1)
      : await db
          .select()
          .from(schema.langgraphCheckpoints)
          .where(
            and(
              eq(schema.langgraphCheckpoints.threadId, threadId),
              eq(schema.langgraphCheckpoints.checkpointNs, checkpointNs),
            ),
          )
          .orderBy(desc(schema.langgraphCheckpoints.id))
          .limit(1);

    if (!row) return undefined;

    const pendingWriteRows = await getPendingWriteRows(
      threadId,
      checkpointNs,
      row.checkpointId,
    );
    return toCheckpointTuple(row, pendingWriteRows);
  }

  async *list(
    config: RunnableConfig,
    options?: CheckpointListOptions,
  ): AsyncGenerator<CheckpointTuple> {
    const rows = await db
      .select()
      .from(schema.langgraphCheckpoints)
      .orderBy(desc(schema.langgraphCheckpoints.id));

    const threadId = getThreadId(config);
    const checkpointNs = config.configurable?.checkpoint_ns;
    const checkpointId = config.configurable?.checkpoint_id;
    const beforeCheckpointId = options?.before?.configurable?.checkpoint_id;
    const metadataFilter = options?.filter;

    let yielded = 0;

    for (const row of rows) {
      if (threadId && row.threadId !== threadId) continue;
      if (typeof checkpointNs === "string" && row.checkpointNs !== checkpointNs) {
        continue;
      }
      if (typeof checkpointId === "string" && row.checkpointId !== checkpointId) {
        continue;
      }
      if (
        typeof beforeCheckpointId === "string" &&
        row.checkpointId >= beforeCheckpointId
      ) {
        continue;
      }

      const metadata = parseJson<CheckpointMetadata>(row.metadata);
      if (metadataFilter) {
        const matches = Object.entries(metadataFilter).every(
          ([key, value]) => (metadata as Record<string, unknown>)[key] === value,
        );
        if (!matches) continue;
      }

      if (typeof options?.limit === "number" && yielded >= options.limit) break;

      const pendingWriteRows = await getPendingWriteRows(
        row.threadId,
        row.checkpointNs,
        row.checkpointId,
      );
      const tuple = toCheckpointTuple(row, pendingWriteRows);
      tuple.metadata = metadata;

      yielded += 1;
      yield tuple;
    }
  }

  async put(
    config: RunnableConfig,
    checkpoint: Checkpoint,
    metadata: CheckpointMetadata,
  ): Promise<RunnableConfig> {
    const threadId = getThreadId(config);
    if (!threadId) {
      throw new Error(
        'Failed to put checkpoint. Missing "configurable.thread_id" in RunnableConfig.',
      );
    }

    const checkpointNs = getCheckpointNs(config);
    const parentCheckpointId = getParentCheckpointId(config);

    await db
      .insert(schema.langgraphCheckpoints)
      .values({
        threadId,
        checkpointNs,
        checkpointId: checkpoint.id,
        parentCheckpointId,
        checkpoint: stringifyJson(checkpoint),
        metadata: stringifyJson(metadata),
      })
      .onConflictDoUpdate({
        target: [
          schema.langgraphCheckpoints.threadId,
          schema.langgraphCheckpoints.checkpointNs,
          schema.langgraphCheckpoints.checkpointId,
        ],
        set: {
          parentCheckpointId,
          checkpoint: stringifyJson(checkpoint),
          metadata: stringifyJson(metadata),
        },
      });

    return {
      configurable: {
        thread_id: threadId,
        checkpoint_ns: checkpointNs,
        checkpoint_id: checkpoint.id,
      },
    };
  }

  async putWrites(
    config: RunnableConfig,
    writes: PendingWrite[],
    taskId: string,
  ): Promise<void> {
    const threadId = getThreadId(config);
    const checkpointId = getCheckpointId(config);

    if (!threadId) {
      throw new Error(
        'Failed to put writes. Missing "configurable.thread_id" in RunnableConfig.',
      );
    }
    if (!checkpointId) {
      throw new Error(
        'Failed to put writes. Missing "configurable.checkpoint_id" in RunnableConfig.',
      );
    }

    const checkpointNs = getCheckpointNs(config);

    for (const [idx, [channel, value]] of writes.entries()) {
      const writeIdx = WRITES_IDX_MAP[channel as string] ?? idx;

      const values = {
        threadId,
        checkpointNs,
        checkpointId,
        taskId,
        idx: writeIdx,
        channel: String(channel),
        value: stringifyJson(value),
      };

      if (writeIdx >= 0) {
        await db
          .insert(schema.langgraphWrites)
          .values(values)
          .onConflictDoNothing({
            target: [
              schema.langgraphWrites.threadId,
              schema.langgraphWrites.checkpointNs,
              schema.langgraphWrites.checkpointId,
              schema.langgraphWrites.taskId,
              schema.langgraphWrites.idx,
            ],
          });
      } else {
        await db
          .insert(schema.langgraphWrites)
          .values(values)
          .onConflictDoUpdate({
            target: [
              schema.langgraphWrites.threadId,
              schema.langgraphWrites.checkpointNs,
              schema.langgraphWrites.checkpointId,
              schema.langgraphWrites.taskId,
              schema.langgraphWrites.idx,
            ],
            set: {
              channel: values.channel,
              value: values.value,
            },
          });
      }
    }
  }

  async deleteThread(threadId: string): Promise<void> {
    await db
      .delete(schema.langgraphWrites)
      .where(eq(schema.langgraphWrites.threadId, threadId));

    await db
      .delete(schema.langgraphCheckpoints)
      .where(eq(schema.langgraphCheckpoints.threadId, threadId));
  }
}

const checkpointer = new SqliteCheckpointSaver();

async function getOrCreateThreadId(userId: number): Promise<string> {
  const [existing] = await db
    .select({ threadId: schema.agentThreads.threadId })
    .from(schema.agentThreads)
    .where(eq(schema.agentThreads.userId, userId))
    .limit(1);

  if (existing?.threadId) return existing.threadId;

  const threadId = crypto.randomUUID();

  try {
    await db.insert(schema.agentThreads).values({
      userId,
      threadId,
    });
    return threadId;
  } catch {
    const [raceWinner] = await db
      .select({ threadId: schema.agentThreads.threadId })
      .from(schema.agentThreads)
      .where(eq(schema.agentThreads.userId, userId))
      .limit(1);

    if (raceWinner?.threadId) return raceWinner.threadId;
    throw new Error(`Failed to create thread for user ${userId}`);
  }
}

export async function getAgentRunnableConfig(
  userId: number,
): Promise<RunnableConfig> {
  const threadId = await getOrCreateThreadId(userId);

  return {
    configurable: {
      thread_id: threadId,
      checkpoint_ns: DEFAULT_CHECKPOINT_NS,
    },
  };
}

export async function resetAgentPersistence(userId: number): Promise<void> {
  const [row] = await db
    .select({ threadId: schema.agentThreads.threadId })
    .from(schema.agentThreads)
    .where(eq(schema.agentThreads.userId, userId))
    .limit(1);

  if (row?.threadId) {
    await checkpointer.deleteThread(row.threadId);
    await db
      .delete(schema.agentThreads)
      .where(eq(schema.agentThreads.userId, userId));
  }
}

export const langGraphCheckpointer = checkpointer;
