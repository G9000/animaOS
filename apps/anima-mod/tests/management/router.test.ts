import { describe, test, expect, beforeAll, afterAll } from "bun:test";
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

  beforeAll(async () => {
    sqlite = new Database(":memory:");
    sqlite.exec(CREATE_TABLES);
    const db = drizzle(sqlite, { schema });

    const configService = new ConfigService(db);
    const stateService = new StateService(db);
    const eventService = new EventService(db);

    // Seed state
    await stateService.setState("test-mod", { enabled: true, status: "running" });

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
    });

    app = new Elysia().use(router);
    client = treaty(app);
  });

  afterAll(() => sqlite.close());

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
  });

  test("PUT /api/mods/:id/config updates config", async () => {
    const { data, error } = await client.api.mods({ id: "test-mod" }).config.put({
      token: "new-token",
      mode: "polling",
    });
    expect(error).toBeNull();
  });

  test("GET /api/mods/:id/health returns status", async () => {
    const { data } = await client.api.mods({ id: "test-mod" }).health.get();
    expect(data!.status).toBe("running");
  });
});
