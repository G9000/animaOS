import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { Database } from "bun:sqlite";
import { drizzle } from "drizzle-orm/bun-sqlite";
import * as schema from "../../src/db/schema.js";
import { ConfigService } from "../../src/management/config-service.js";
import type { ModConfigSchema } from "../../src/core/types.js";
import {
  credentialReference,
  type ModCredentialStore,
} from "../../src/security/credential-broker.js";

class MemoryCredentialStore implements ModCredentialStore {
  values = new Map<string, string>();

  reference(scope: string, name: string): string {
    return credentialReference(scope, name);
  }

  async put(reference: string, secret: string): Promise<void> {
    this.values.set(reference, secret);
  }

  async resolve(references: string[]): Promise<Record<string, string>> {
    return Object.fromEntries(
      references.map((reference) => {
        const value = this.values.get(reference);
        if (value === undefined) throw new Error("unavailable secret");
        return [reference, value];
      }),
    );
  }

  async delete(reference: string): Promise<void> {
    this.values.delete(reference);
  }
}

const telegramSchema: ModConfigSchema = {
  token: { type: "secret", label: "Bot Token", required: true },
  mode: { type: "enum", label: "Mode", options: ["polling", "webhook"], default: "polling" },
  webhookUrl: { type: "string", label: "Webhook URL", showWhen: { mode: "webhook" } },
};

describe("ConfigService", () => {
  let sqlite: Database;
  let db: ReturnType<typeof drizzle>;
  let service: ConfigService;
  let credentials: MemoryCredentialStore;

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
    credentials = new MemoryCredentialStore();
    service = new ConfigService(db, credentials);
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

  test("secret config stores only an opaque verified credential reference", async () => {
    await service.setConfig("telegram", { token: "abc123" }, telegramSchema);
    const row = sqlite.query("SELECT value FROM mod_config WHERE mod_id = 'telegram'").get() as {
      value: string;
    };
    expect(row.value).not.toContain("abc123");
    expect(JSON.parse(row.value)).toMatch(/^anima-credential:v1:[0-9a-f]{64}$/);
    expect([...credentials.values.values()]).toEqual([JSON.stringify("abc123")]);
  });

  test("legacy plaintext secret rows migrate copy-verify-scrub on read", async () => {
    sqlite.query(
      "INSERT INTO mod_config (mod_id, key, value, is_secret) VALUES (?, ?, ?, 1)",
    ).run("telegram", "token", JSON.stringify("legacy-secret"));

    expect((await service.getConfig("telegram")).token).toBe("legacy-secret");
    const row = sqlite.query("SELECT value FROM mod_config WHERE mod_id = 'telegram'").get() as {
      value: string;
    };
    expect(row.value).not.toContain("legacy-secret");
  });

  test("setConfig validates required fields", async () => {
    await expect(
      service.setConfig("telegram", { mode: "polling" }, telegramSchema)
    ).rejects.toThrow(/required/i);
  });

  test("setConfig validates partial updates against existing config", async () => {
    await service.setConfig("telegram", { token: "abc123", mode: "polling" }, telegramSchema);
    await service.setConfig("telegram", { mode: "webhook" }, telegramSchema);

    const config = await service.getConfig("telegram", { maskSecrets: false });
    expect(config).toEqual({ token: "abc123", mode: "webhook" });
  });

  test("setConfig validates enum values", async () => {
    await expect(
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
