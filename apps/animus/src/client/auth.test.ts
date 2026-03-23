import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { readConfig, writeConfig, type AnimusConfig } from "./auth";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("auth config", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = mkdtempSync(join(tmpdir(), "animus-test-"));
  });

  afterEach(() => {
    rmSync(tempDir, { recursive: true, force: true });
  });

  test("readConfig returns null when no config exists", () => {
    const config = readConfig(join(tempDir, "config.json"));
    expect(config).toBeNull();
  });

  test("writeConfig creates file and readConfig reads it back", () => {
    const path = join(tempDir, "config.json");
    const config: AnimusConfig = {
      serverUrl: "ws://localhost:3031",
      unlockToken: "test_token",
      username: "leo",
    };
    writeConfig(path, config);
    const read = readConfig(path);
    expect(read).toEqual(config);
  });
});
