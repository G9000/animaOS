import { eq, desc } from "drizzle-orm";
import { modEvents } from "../db/schema.js";

import type { BunSQLiteDatabase } from "drizzle-orm/bun-sqlite";
import type * as schema from "../db/schema.js";

export type ModEventType = "config_changed" | "started" | "stopped" | "error";

export interface ModEvent {
  id: number;
  modId: string;
  eventType: ModEventType;
  detail: Record<string, unknown> | null;
  createdAt: string | null;
}

type ModEventRow = typeof modEvents.$inferSelect;

function parseDetail(value: string | null): Record<string, unknown> | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : { value: parsed };
  } catch {
    return { message: value };
  }
}

function normalizeEvent(row: ModEventRow): ModEvent {
  return {
    id: row.id,
    modId: row.modId,
    eventType: row.eventType,
    detail: parseDetail(row.detail),
    createdAt: row.createdAt ?? null,
  };
}

export class EventService {
  constructor(private db: BunSQLiteDatabase<typeof schema>) {}

  async logEvent(
    modId: string,
    eventType: ModEventType,
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

  async getEvents(modId: string, limit = 50): Promise<ModEvent[]> {
    return this.db
      .select()
      .from(modEvents)
      .where(eq(modEvents.modId, modId))
      .orderBy(desc(modEvents.id))
      .limit(limit)
      .all()
      .map(normalizeEvent);
  }
}
