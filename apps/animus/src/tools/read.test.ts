import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { executeRead } from "./read";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("read tool", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = mkdtempSync(join(tmpdir(), "animus-read-test-"));
  });

  afterEach(() => {
    rmSync(tempDir, { recursive: true, force: true });
  });

  test("reads file with line numbers", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "line1\nline2\nline3", "utf-8");

    const result = executeRead({ file_path: filePath });
    expect(result.status).toBe("success");
    expect(result.result).toContain("1| line1");
    expect(result.result).toContain("2| line2");
    expect(result.result).toContain("3| line3");
  });

  test("offset skips lines", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "line1\nline2\nline3\nline4\nline5", "utf-8");

    const result = executeRead({ file_path: filePath, offset: 2 });
    expect(result.status).toBe("success");
    // Should start at line 3 (0-indexed offset=2)
    expect(result.result).toContain("3| line3");
    expect(result.result).not.toContain("1| line1");
    expect(result.result).not.toContain("2| line2");
  });

  test("limit restricts lines returned", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "line1\nline2\nline3\nline4\nline5", "utf-8");

    const result = executeRead({ file_path: filePath, limit: 2 });
    expect(result.status).toBe("success");
    expect(result.result).toContain("1| line1");
    expect(result.result).toContain("2| line2");
    expect(result.result).not.toContain("3| line3");
  });

  test("offset + limit combined", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "a\nb\nc\nd\ne", "utf-8");

    const result = executeRead({ file_path: filePath, offset: 1, limit: 2 });
    expect(result.status).toBe("success");
    expect(result.result).toContain("2| b");
    expect(result.result).toContain("3| c");
    expect(result.result).not.toContain("1| a");
    expect(result.result).not.toContain("4| d");
  });

  test("file not found returns error", () => {
    const result = executeRead({ file_path: join(tempDir, "nonexistent.txt") });
    expect(result.status).toBe("error");
    expect(result.result).toContain("File not found");
  });

  test("empty file returns success", () => {
    const filePath = join(tempDir, "empty.txt");
    writeFileSync(filePath, "", "utf-8");

    const result = executeRead({ file_path: filePath });
    expect(result.status).toBe("success");
  });
});
