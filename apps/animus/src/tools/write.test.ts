import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { executeWrite } from "./write";
import { mkdtempSync, rmSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("write tool", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = mkdtempSync(join(tmpdir(), "animus-write-test-"));
  });

  afterEach(() => {
    rmSync(tempDir, { recursive: true, force: true });
  });

  test("writes content to file", () => {
    const filePath = join(tempDir, "output.txt");
    const result = executeWrite({ file_path: filePath, content: "hello world" });

    expect(result.status).toBe("success");
    expect(readFileSync(filePath, "utf-8")).toBe("hello world");
  });

  test("creates parent directories", () => {
    const filePath = join(tempDir, "a", "b", "c", "deep.txt");
    const result = executeWrite({ file_path: filePath, content: "nested" });

    expect(result.status).toBe("success");
    expect(existsSync(filePath)).toBe(true);
    expect(readFileSync(filePath, "utf-8")).toBe("nested");
  });

  test("reports character count", () => {
    const filePath = join(tempDir, "count.txt");
    const content = "twelve chars";
    const result = executeWrite({ file_path: filePath, content });

    expect(result.status).toBe("success");
    expect(result.result).toContain(`${content.length} chars`);
  });

  test("overwrites existing file", () => {
    const filePath = join(tempDir, "overwrite.txt");
    executeWrite({ file_path: filePath, content: "first" });
    executeWrite({ file_path: filePath, content: "second" });

    expect(readFileSync(filePath, "utf-8")).toBe("second");
  });
});
