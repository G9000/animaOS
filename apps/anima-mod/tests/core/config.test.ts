/**
 * Config Loader Tests
 */

import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import {
  loadConfig,
  clearConfigCache,
  migrateLiteralCorePassword,
  scrubLiteralModSecrets,
} from "../../src/core/config.js";
import { readFile, writeFile, unlink } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  credentialReference,
  type ModCredentialStore,
} from "../../src/security/credential-broker.js";

const TEST_CONFIG_PATH = join(tmpdir(), "anima-mod-test-config.yaml");

class MemoryCredentials implements ModCredentialStore {
  readonly values = new Map<string, string>();

  reference(scope: string, name: string): string {
    return credentialReference(`anima-mod:core.${scope}`, name);
  }

  async put(reference: string, secret: string): Promise<void> {
    this.values.set(reference, secret);
  }

  async resolve(references: string[]): Promise<Record<string, string>> {
    return Object.fromEntries(
      references.flatMap((reference) => {
        const value = this.values.get(reference);
        return value === undefined ? [] : [[reference, value]];
      }),
    );
  }

  async delete(reference: string): Promise<void> {
    this.values.delete(reference);
  }
}

describe("loadConfig", () => {
  beforeEach(async () => {
    clearConfigCache();
    // Clean up any existing test file
    try {
      await unlink(TEST_CONFIG_PATH);
    } catch {
      // Ignore if doesn't exist
    }
  });

  afterEach(async () => {
    clearConfigCache();
    try {
      await unlink(TEST_CONFIG_PATH);
    } catch {
      // Ignore
    }
  });

  it("should return default config when file doesn't exist", async () => {
    const config = await loadConfig("/nonexistent/config.yaml");
    expect(config.modules).toEqual([]);
  });

  it("should parse YAML config correctly", async () => {
    const yamlContent = `
modules:
  - id: test-mod
    path: ./mods/test
    config:
      key: value
      number: 42

core:
  port: 3034
  anima:
    baseUrl: http://localhost:3031/api
`;
    await writeFile(TEST_CONFIG_PATH, yamlContent, "utf-8");

    const config = await loadConfig(TEST_CONFIG_PATH);

    expect(config.modules).toHaveLength(1);
    expect(config.modules?.[0].id).toBe("test-mod");
    expect(config.modules?.[0].config.key).toBe("value");
    expect(config.core?.port).toBe(3034);
    expect(config.core?.anima?.baseUrl).toBe("http://localhost:3031/api");
  });

  it("should substitute environment variables", async () => {
    process.env.TEST_TOKEN = "secret-token-123";
    
    const yamlContent = `
modules:
  - id: telegram
    path: ./mods/telegram
    config:
      token: \${TEST_TOKEN}
`;
    await writeFile(TEST_CONFIG_PATH, yamlContent, "utf-8");

    const config = await loadConfig(TEST_CONFIG_PATH);

    expect(config.modules?.[0].config.token).toBe("secret-token-123");
    
    delete process.env.TEST_TOKEN;
  });

  it("should use default values for missing env vars", async () => {
    const yamlContent = `
modules:
  - id: test
    config:
      value: \${MISSING_VAR:-default_value}
`;
    await writeFile(TEST_CONFIG_PATH, yamlContent, "utf-8");

    const config = await loadConfig(TEST_CONFIG_PATH);

    expect(config.modules?.[0].config.value).toBe("default_value");
  });

  it("scrubs literal module secrets but preserves environment placeholders", async () => {
    await writeFile(
      TEST_CONFIG_PATH,
      `modules:
  - id: google
    path: ./mods/google
    config:
      clientId: literal-client-id
      clientSecret: \${GOOGLE_CLIENT_SECRET}
      redirectUri: http://127.0.0.1/callback
`,
      "utf-8",
    );

    expect(
      await scrubLiteralModSecrets(TEST_CONFIG_PATH, "google", [
        "clientId",
        "clientSecret",
      ]),
    ).toBe(true);

    const source = await readFile(TEST_CONFIG_PATH, "utf-8");
    expect(source).not.toContain("literal-client-id");
    expect(source).toContain("${GOOGLE_CLIENT_SECRET}");
    const config = await loadConfig(TEST_CONFIG_PATH);
    expect(config.modules?.[0].config.clientId).toBeUndefined();
  });

  it("copy-verifies and scrubs a literal Core password", async () => {
    const credentials = new MemoryCredentials();
    await writeFile(
      TEST_CONFIG_PATH,
      `modules: []
core:
  anima:
    username: private-user
    password: literal-core-password
`,
      "utf-8",
    );

    expect(
      await migrateLiteralCorePassword(TEST_CONFIG_PATH, credentials),
    ).toBe(true);

    const source = await readFile(TEST_CONFIG_PATH, "utf-8");
    expect(source).not.toContain("literal-core-password");
    const config = await loadConfig(TEST_CONFIG_PATH);
    expect(config.core?.anima?.password).toBeUndefined();
    expect(config.core?.anima?.passwordRef).toMatch(
      /^anima-credential:v1:[0-9a-f]{64}$/,
    );
    expect([...credentials.values.values()]).toEqual([
      JSON.stringify("literal-core-password"),
    ]);
  });
});
