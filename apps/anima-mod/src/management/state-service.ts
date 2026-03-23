import { eq } from "drizzle-orm";
import { modState } from "../db/schema.js";

interface ModState {
  modId: string;
  enabled: boolean;
  status: "stopped" | "running" | "error";
  lastError: string | null;
  startedAt: string | null;
  updatedAt: string | null;
}

import type { BunSQLiteDatabase } from "drizzle-orm/bun-sqlite";
import type * as schema from "../db/schema.js";

export class StateService {
  constructor(private db: BunSQLiteDatabase<typeof schema>) {}

  async getState(modId: string): Promise<ModState | null> {
    const rows = this.db
      .select()
      .from(modState)
      .where(eq(modState.modId, modId))
      .all();
    return rows[0] ?? null;
  }

  async setState(modId: string, updates: Partial<Omit<ModState, "modId">>): Promise<void> {
    const existing = await this.getState(modId);

    if (existing) {
      this.db
        .update(modState)
        .set({ ...updates, updatedAt: new Date().toISOString() })
        .where(eq(modState.modId, modId))
        .run();
    } else {
      this.db
        .insert(modState)
        .values({ modId, ...updates })
        .run();
    }
  }

  async getAllStates(): Promise<ModState[]> {
    return this.db.select().from(modState).all();
  }
}
