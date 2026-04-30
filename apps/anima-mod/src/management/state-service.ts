import { eq } from "drizzle-orm";
import { modState } from "../db/schema.js";

export type ModStatus = "stopped" | "running" | "error";

export interface ModState {
  modId: string;
  enabled: boolean;
  status: ModStatus;
  lastError: string | null;
  startedAt: string | null;
  updatedAt: string | null;
}

import type { BunSQLiteDatabase } from "drizzle-orm/bun-sqlite";
import type * as schema from "../db/schema.js";

type ModStateRow = typeof modState.$inferSelect;

function normalizeState(row: ModStateRow): ModState {
  return {
    modId: row.modId,
    enabled: row.enabled ?? false,
    status: row.status ?? "stopped",
    lastError: row.lastError ?? null,
    startedAt: row.startedAt ?? null,
    updatedAt: row.updatedAt ?? null,
  };
}

export class StateService {
  constructor(private db: BunSQLiteDatabase<typeof schema>) {}

  async getState(modId: string): Promise<ModState | null> {
    const rows = this.db
      .select()
      .from(modState)
      .where(eq(modState.modId, modId))
      .all();
    return rows[0] ? normalizeState(rows[0]) : null;
  }

  async setState(modId: string, updates: Partial<Omit<ModState, "modId">>): Promise<void> {
    const existing = await this.getState(modId);
    const updatedAt = new Date().toISOString();

    if (existing) {
      this.db
        .update(modState)
        .set({ ...updates, updatedAt })
        .where(eq(modState.modId, modId))
        .run();
    } else {
      this.db
        .insert(modState)
        .values({
          modId,
          enabled: updates.enabled ?? false,
          status: updates.status ?? "stopped",
          lastError: updates.lastError ?? null,
          startedAt: updates.startedAt ?? null,
          updatedAt,
        })
        .run();
    }
  }

  async getAllStates(): Promise<ModState[]> {
    return this.db.select().from(modState).all().map(normalizeState);
  }
}
