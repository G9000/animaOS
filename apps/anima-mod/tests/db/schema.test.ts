import { describe, test, expect, beforeAll, afterAll } from "bun:test";
import { Database } from "bun:sqlite";
import { drizzle } from "drizzle-orm/bun-sqlite";
import { sql } from "drizzle-orm";
import { modConfig, modState, modEvents, modStore } from "../../src/db/schema.js";

describe("database schema", () => {
  let sqlite: Database;
  let db: ReturnType<typeof drizzle>;

  beforeAll(() => {
    sqlite = new Database(":memory:");
    db = drizzle(sqlite);
    sqlite.exec(`
      CREATE TABLE mod_config (
        mod_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        is_secret INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (mod_id, key)
      );
      CREATE TABLE mod_state (
        mod_id TEXT PRIMARY KEY,
        enabled INTEGER DEFAULT 0,
        status TEXT DEFAULT 'stopped',
        last_error TEXT,
        started_at TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
      );
      CREATE TABLE mod_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mod_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        detail TEXT,
        created_at TEXT DEFAULT (datetime('now'))
      );
      CREATE TABLE mod_store (
        namespace TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (namespace, key)
      );
    `);
  });

  afterAll(() => sqlite.close());

  test("mod_config: insert and read", () => {
    db.insert(modConfig).values({
      modId: "telegram",
      key: "token",
      value: JSON.stringify("abc123"),
      isSecret: true,
    }).run();

    const rows = db.select().from(modConfig).all();
    expect(rows).toHaveLength(1);
    expect(rows[0].modId).toBe("telegram");
    expect(rows[0].isSecret).toBe(true);
  });

  test("mod_state: insert and read", () => {
    db.insert(modState).values({
      modId: "telegram",
      enabled: true,
      status: "running",
    }).run();

    const rows = db.select().from(modState).all();
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe("running");
  });

  test("mod_events: insert and read", () => {
    db.insert(modEvents).values({
      modId: "telegram",
      eventType: "started",
      detail: JSON.stringify({ version: "1.0.0" }),
    }).run();

    const rows = db.select().from(modEvents).all();
    expect(rows).toHaveLength(1);
    expect(rows[0].eventType).toBe("started");
  });

  test("mod_store: insert and read (Drizzle-managed)", () => {
    db.insert(modStore).values({
      namespace: "telegram",
      key: "last_poll",
      value: JSON.stringify(Date.now()),
    }).run();

    const rows = db.select().from(modStore).all();
    expect(rows).toHaveLength(1);
  });
});
