import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { Elysia } from "elysia";
import { treaty } from "@elysiajs/eden";
import { Database } from "bun:sqlite";
import { drizzle } from "drizzle-orm/bun-sqlite";
import * as schema from "../../src/db/schema.js";
import { createManagementRouter } from "../../src/management/router.js";
import { ConfigService } from "../../src/management/config-service.js";
import { StateService } from "../../src/management/state-service.js";
import { EventService } from "../../src/management/event-service.js";
import type { Mod, ModConfigSchema } from "../../src/core/types.js";

const CREATE_TABLES = `
  CREATE TABLE mod_config (mod_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, is_secret INTEGER DEFAULT 0, updated_at TEXT, PRIMARY KEY (mod_id, key));
  CREATE TABLE mod_state (mod_id TEXT PRIMARY KEY, enabled INTEGER DEFAULT 0, status TEXT DEFAULT 'stopped', last_error TEXT, started_at TEXT, updated_at TEXT);
  CREATE TABLE mod_events (id INTEGER PRIMARY KEY AUTOINCREMENT, mod_id TEXT NOT NULL, event_type TEXT NOT NULL, detail TEXT, created_at TEXT);
`;

const testSchema: ModConfigSchema = {
  token: { type: "secret", label: "Token", required: true },
  mode: { type: "enum", label: "Mode", options: ["polling", "webhook"], default: "polling" },
};

const fakeMod: Mod = {
  id: "test-mod",
  version: "1.0.0",
  configSchema: testSchema,
  setupGuide: [
    { step: 1, title: "Setup", field: "token" },
  ],
  async init() {},
  async start() {},
  async stop() {},
};

describe("Management API", () => {
  let sqlite: Database;
  let app: Elysia;
  let client: ReturnType<typeof treaty>;
  let stateService: StateService;
  let eventService: EventService;

  beforeEach(async () => {
    sqlite = new Database(":memory:");
    sqlite.exec(CREATE_TABLES);
    const db = drizzle(sqlite, { schema });

    const configService = new ConfigService(db);
    stateService = new StateService(db);
    eventService = new EventService(db);

    // Seed state
    await stateService.setState("test-mod", { enabled: true, status: "running" });
    await eventService.logEvent("test-mod", "started", { source: "test" });

    const modsMap = new Map([
      ["test-mod", {
        config: { id: "test-mod", path: "./mods/test", config: {} },
        mod: fakeMod,
      }],
    ]);

    const router = createManagementRouter({
      mods: modsMap as any,
      configService,
      stateService,
      eventService,
      onEnable: async (id) => {
        await stateService.setState(id, {
          enabled: true,
          status: "running",
          startedAt: "2026-04-30T00:00:00.000Z",
          lastError: null,
        });
        await eventService.logEvent(id, "started");
      },
      onDisable: async (id) => {
        await stateService.setState(id, { enabled: false, status: "stopped" });
        await eventService.logEvent(id, "stopped");
      },
      onRestart: async (id) => {
        await stateService.setState(id, {
          enabled: true,
          status: "running",
          startedAt: "2026-04-30T00:00:00.000Z",
          lastError: null,
        });
        await eventService.logEvent(id, "started");
      },
    });

    app = new Elysia().use(router);
    client = treaty(app);
  });

  afterEach(() => sqlite.close());

  test("GET /api/mods lists all mods", async () => {
    const { data } = await client.api.mods.get();
    expect(data).toHaveLength(1);
    expect(data![0].id).toBe("test-mod");
    expect(data![0].hasConfigSchema).toBe(true);
    expect(data![0].hasSetupGuide).toBe(true);
  });

  test("GET /api/mods/:id returns full detail", async () => {
    const { data } = await client.api.mods({ id: "test-mod" }).get();
    expect(data!.id).toBe("test-mod");
    expect(data!.configSchema).toBeDefined();
    expect(data!.setupGuide).toHaveLength(1);
    expect(data!.events).toHaveLength(1);
    expect(data!.events[0].detail).toEqual({ source: "test" });
  });

  test("PUT /api/mods/:id/config updates config", async () => {
    const { data, error } = await client.api.mods({ id: "test-mod" }).config.put({
      token: "new-token",
      mode: "polling",
    });
    expect(error).toBeNull();
    expect(data!.state.status).toBe("running");

    const events = await client.api.mods({ id: "test-mod" }).events.get();
    const configEvent = events.data!.find((event) => event.eventType === "config_changed");
    expect(configEvent!.detail).toEqual({ fields: ["mode", "token"] });
    expect(JSON.stringify(configEvent!.detail)).not.toContain("new-token");
  });

  test("GET /api/mods/:id/health returns status", async () => {
    const { data } = await client.api.mods({ id: "test-mod" }).health.get();
    expect(data!.status).toBe("running");
  });

  test("GET /api/mods/:id/events returns recent events", async () => {
    await eventService.logEvent("test-mod", "config_changed", { key: "token" });

    const { data } = await client.api.mods({ id: "test-mod" }).events.get();

    expect(data).toHaveLength(2);
    expect(data![0].eventType).toBe("config_changed");
    expect(data![0].detail).toEqual({ key: "token" });
  });

  test("GET /api/mods/:id/events ignores invalid limits", async () => {
    const response = await app.handle(
      new Request("http://localhost/api/mods/test-mod/events?limit=not-a-number"),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toHaveLength(1);
  });

  test("POST /api/mods/:id/disable returns refreshed state", async () => {
    const { data, error } = await client.api.mods({ id: "test-mod" }).disable.post();

    expect(error).toBeNull();
    expect(data!.status).toBe("stopped");
    expect(data!.enabled).toBe(false);
  });

  test("POST /api/mods/:id/enable returns refreshed state", async () => {
    await stateService.setState("test-mod", { enabled: false, status: "stopped" });

    const { data, error } = await client.api.mods({ id: "test-mod" }).enable.post();

    expect(error).toBeNull();
    expect(data!.status).toBe("running");
    expect(data!.enabled).toBe(true);
  });

  test("POST /api/mods/:id/restart returns refreshed state", async () => {
    const { data, error } = await client.api.mods({ id: "test-mod" }).restart.post();

    expect(error).toBeNull();
    expect(data!.status).toBe("running");
    expect(data!.enabled).toBe(true);
  });
});
