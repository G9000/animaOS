/**
 * a-mod: Module Store
 *
 * Drizzle-backed KV store for module-private data.
 * Uses the shared DB singleton from db/index.ts.
 */

import type { ModStore } from "./types.js";
import { eq, and } from "drizzle-orm";
import { modStore } from "../db/schema.js";
import { getDb } from "../db/index.js";

export class ModStoreImpl implements ModStore {
  private namespace: string;

  constructor(modId: string) {
    this.namespace = modId;
  }

  async get<T>(key: string): Promise<T | null> {
    const db = getDb();
    const rows = db
      .select()
      .from(modStore)
      .where(and(eq(modStore.namespace, this.namespace), eq(modStore.key, key)))
      .all();
    if (rows.length === 0) return null;
    return JSON.parse(rows[0].value) as T;
  }

  async set<T>(key: string, value: T): Promise<void> {
    const db = getDb();
    db.insert(modStore)
      .values({
        namespace: this.namespace,
        key,
        value: JSON.stringify(value),
      })
      .onConflictDoUpdate({
        target: [modStore.namespace, modStore.key],
        set: {
          value: JSON.stringify(value),
          updatedAt: new Date().toISOString(),
        },
      })
      .run();
  }

  async delete(key: string): Promise<void> {
    const db = getDb();
    db.delete(modStore)
      .where(and(eq(modStore.namespace, this.namespace), eq(modStore.key, key)))
      .run();
  }

  async has(key: string): Promise<boolean> {
    const db = getDb();
    const rows = db
      .select()
      .from(modStore)
      .where(and(eq(modStore.namespace, this.namespace), eq(modStore.key, key)))
      .all();
    return rows.length > 0;
  }
}
