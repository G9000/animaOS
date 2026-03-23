import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { Database } from "bun:sqlite";
import { drizzle } from "drizzle-orm/bun-sqlite";
import * as schema from "../../src/db/schema.js";
import { StateService } from "../../src/management/state-service.js";
import { EventService } from "../../src/management/event-service.js";

describe("StateService", () => {
  let sqlite: Database;
  let db: ReturnType<typeof drizzle>;
  let stateService: StateService;

  beforeEach(() => {
    sqlite = new Database(":memory:");
    sqlite.exec(`
      CREATE TABLE mod_state (
        mod_id TEXT PRIMARY KEY, enabled INTEGER DEFAULT 0,
        status TEXT DEFAULT 'stopped', last_error TEXT,
        started_at TEXT, updated_at TEXT DEFAULT (datetime('now'))
      );
    `);
    db = drizzle(sqlite, { schema });
    stateService = new StateService(db);
  });

  afterEach(() => sqlite.close());

  test("getState returns default for unknown mod", async () => {
    const state = await stateService.getState("telegram");
    expect(state).toBeNull();
  });

  test("setState creates new state", async () => {
    await stateService.setState("telegram", { enabled: true, status: "running" });
    const state = await stateService.getState("telegram");
    expect(state?.enabled).toBe(true);
    expect(state?.status).toBe("running");
  });

  test("setState updates existing state", async () => {
    await stateService.setState("telegram", { enabled: true, status: "running" });
    await stateService.setState("telegram", { status: "error", lastError: "connection lost" });
    const state = await stateService.getState("telegram");
    expect(state?.status).toBe("error");
    expect(state?.lastError).toBe("connection lost");
  });

  test("getAllStates returns all mod states", async () => {
    await stateService.setState("telegram", { enabled: true, status: "running" });
    await stateService.setState("discord", { enabled: false, status: "stopped" });
    const states = await stateService.getAllStates();
    expect(states).toHaveLength(2);
  });
});

describe("EventService", () => {
  let sqlite: Database;
  let db: ReturnType<typeof drizzle>;
  let eventService: EventService;

  beforeEach(() => {
    sqlite = new Database(":memory:");
    sqlite.exec(`
      CREATE TABLE mod_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mod_id TEXT NOT NULL,
        event_type TEXT NOT NULL, detail TEXT,
        created_at TEXT DEFAULT (datetime('now'))
      );
    `);
    db = drizzle(sqlite, { schema });
    eventService = new EventService(db);
  });

  afterEach(() => sqlite.close());

  test("logEvent creates event", async () => {
    await eventService.logEvent("telegram", "started", { version: "1.0.0" });
    const events = await eventService.getEvents("telegram");
    expect(events).toHaveLength(1);
    expect(events[0].eventType).toBe("started");
  });

  test("getEvents returns events in reverse chronological order", async () => {
    await eventService.logEvent("telegram", "started");
    await eventService.logEvent("telegram", "config_changed", { key: "mode" });
    const events = await eventService.getEvents("telegram");
    expect(events).toHaveLength(2);
  });
});
