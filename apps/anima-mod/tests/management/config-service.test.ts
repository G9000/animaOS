import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { Database } from "bun:sqlite";
import { drizzle } from "drizzle-orm/bun-sqlite";
import * as schema from "../../src/db/schema.js";
import { ConfigService } from "../../src/management/config-service.js";
import type { ModConfigSchema } from "../../src/core/types.js";

const telegramSchema: ModConfigSchema = {
  token: { type: "secret", label: "Bot Token", required: true },
  mode: { type: "enum", label: "Mode", options: ["polling", "webhook"], default: "polling" },
  webhookUrl: { type: "string", label: "Webhook URL", showWhen: { mode: "webhook" } },
};

describe("ConfigService", () => {
  let sqlite: Database;
  let db: ReturnType<typeof drizzle>;
  let service: ConfigService;

  beforeEach(() => {
    sqlite = new Database(":memory:");
    sqlite.exec(`
      CREATE TABLE mod_config (
        mod_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
        is_secret INTEGER DEFAULT 0, updated_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (mod_id, key)
      );
    `);
    db = drizzle(sqlite, { schema });
    service = new ConfigService(db);
  });

  afterEach(() => sqlite.close());

  test("setConfig writes values to DB", async () => {
    await service.setConfig("telegram", { token: "abc123", mode: "polling" }, telegramSchema);
    const config = await service.getConfig("telegram");
    expect(config.mode).toBe("polling");
  });

  test("getConfig masks secrets", async () => {
    await service.setConfig("telegram", { token: "abc123" }, telegramSchema);
    const config = await service.getConfig("telegram", { maskSecrets: true });
    expect(config.token).toBe("***");
  });

  test("getConfig returns raw values when not masking", async () => {
    await service.setConfig("telegram", { token: "abc123" }, telegramSchema);
    const config = await service.getConfig("telegram", { maskSecrets: false });
    expect(config.token).toBe("abc123");
  });

  test("setConfig validates required fields", () => {
    expect(
      service.setConfig("telegram", { mode: "polling" }, telegramSchema)
    ).rejects.toThrow(/required/i);
  });

  test("setConfig validates enum values", () => {
    expect(
      service.setConfig("telegram", { token: "abc", mode: "invalid" }, telegramSchema)
    ).rejects.toThrow(/invalid.*mode/i);
  });

  test("hasConfig returns false for unconfigured mod", async () => {
    expect(await service.hasConfig("telegram")).toBe(false);
  });

  test("hasConfig returns true after config set", async () => {
    await service.setConfig("telegram", { token: "abc" }, telegramSchema);
    expect(await service.hasConfig("telegram")).toBe(true);
  });
});
