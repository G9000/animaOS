import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { loadPermissionRules, matchesRule } from "./permission_rules";

describe("matchesRule", () => {
  test("matches tool name exactly", () => {
    expect(matchesRule("write_file", "write_file", {})).toBe(true);
    expect(matchesRule("write_file", "read_file", {})).toBe(false);
  });

  test("matches bash exact command", () => {
    expect(matchesRule("bash:npm test", "bash", { command: "npm test" })).toBe(true);
    expect(matchesRule("bash:npm test", "bash", { command: "npm run build" })).toBe(false);
  });

  test("matches bash glob prefix", () => {
    expect(matchesRule("bash:npm *", "bash", { command: "npm test" })).toBe(true);
    expect(matchesRule("bash:npm *", "bash", { command: "npm run build" })).toBe(true);
    expect(matchesRule("bash:npm *", "bash", { command: "bun test" })).toBe(false);
  });

  test("bash rule doesn't match non-bash tool", () => {
    expect(matchesRule("bash:ls", "write_file", { command: "ls" })).toBe(false);
  });

  test("handles missing command gracefully", () => {
    expect(matchesRule("bash:ls", "bash", {})).toBe(false);
  });
});

describe("loadPermissionRules", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "perm-rules-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("returns empty rules when no config exists", () => {
    const rules = loadPermissionRules(dir);
    expect(rules.allow).toEqual([]);
    expect(rules.deny).toEqual([]);
  });

  test("loads project-local rules", () => {
    const animusDir = join(dir, ".animus");
    mkdirSync(animusDir, { recursive: true });
    writeFileSync(
      join(animusDir, "permissions.json"),
      JSON.stringify({
        allow: ["bash:npm test", "write_file"],
        deny: ["bash:rm *"],
      }),
    );

    const rules = loadPermissionRules(dir);
    expect(rules.allow).toContain("bash:npm test");
    expect(rules.allow).toContain("write_file");
    expect(rules.deny).toContain("bash:rm *");
  });

  test("handles malformed JSON gracefully", () => {
    const animusDir = join(dir, ".animus");
    mkdirSync(animusDir, { recursive: true });
    writeFileSync(join(animusDir, "permissions.json"), "not json{{{");

    const rules = loadPermissionRules(dir);
    expect(rules.allow).toEqual([]);
    expect(rules.deny).toEqual([]);
  });

  test("handles missing allow/deny keys", () => {
    const animusDir = join(dir, ".animus");
    mkdirSync(animusDir, { recursive: true });
    writeFileSync(join(animusDir, "permissions.json"), JSON.stringify({ allow: ["bash:ls"] }));

    const rules = loadPermissionRules(dir);
    expect(rules.allow).toContain("bash:ls");
    expect(rules.deny).toEqual([]);
  });
});
