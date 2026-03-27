import { describe, test, expect, beforeEach, afterEach, beforeAll } from "bun:test";
import { executeGrep } from "./grep";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { execFileSync } from "node:child_process";

// Check if ripgrep is available
let hasRg = false;
try {
  execFileSync("rg", ["--version"]);
  hasRg = true;
} catch {}

const describeIfRg = hasRg ? describe : describe.skip;

describeIfRg("grep tool", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = mkdtempSync(join(tmpdir(), "animus-grep-test-"));
  });

  afterEach(() => {
    rmSync(tempDir, { recursive: true, force: true });
  });

  test("finds matching pattern", () => {
    writeFileSync(join(tempDir, "file.txt"), "hello world\nfoo bar\nhello again", "utf-8");

    const result = executeGrep({ pattern: "hello", path: tempDir });
    expect(result.status).toBe("success");
    expect(result.result).toContain("hello");
  });

  test("no matches returns success with message", () => {
    writeFileSync(join(tempDir, "file.txt"), "some content", "utf-8");

    const result = executeGrep({ pattern: "nonexistent_xyz_123", path: tempDir });
    expect(result.status).toBe("success");
    expect(result.result).toBe("No matches found");
  });

  test("include filter works", () => {
    writeFileSync(join(tempDir, "code.ts"), "function hello() {}", "utf-8");
    writeFileSync(join(tempDir, "code.js"), "function hello() {}", "utf-8");

    const result = executeGrep({ pattern: "hello", path: tempDir, include: "*.ts" });
    expect(result.status).toBe("success");
    expect(result.result).toContain("code.ts");
    expect(result.result).not.toContain("code.js");
  });

  test("returns line numbers", () => {
    writeFileSync(join(tempDir, "file.txt"), "line1\nline2\nmatching line\nline4", "utf-8");

    const result = executeGrep({ pattern: "matching", path: tempDir });
    expect(result.status).toBe("success");
    // ripgrep with --line-number shows "3:" for line 3
    expect(result.result).toContain("3:");
  });
});
