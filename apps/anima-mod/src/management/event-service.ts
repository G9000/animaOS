import { eq, desc } from "drizzle-orm";
import { modEvents } from "../db/schema.js";

import type { BunSQLiteDatabase } from "drizzle-orm/bun-sqlite";
import type * as schema from "../db/schema.js";

export class EventService {
  constructor(private db: BunSQLiteDatabase<typeof schema>) {}

  async logEvent(
    modId: string,
    eventType: string,
    detail?: Record<string, unknown>
  ): Promise<void> {
    this.db
      .insert(modEvents)
      .values({
        modId,
        eventType,
        detail: detail ? JSON.stringify(detail) : null,
      })
      .run();
  }

  async getEvents(modId: string, limit = 50): Promise<any[]> {
    return this.db
      .select()
      .from(modEvents)
      .where(eq(modEvents.modId, modId))
      .orderBy(desc(modEvents.id))
      .limit(limit)
      .all();
  }
}
