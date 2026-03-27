import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { executeEdit } from "./edit";
import { mkdtempSync, rmSync, writeFileSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("edit tool", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = mkdtempSync(join(tmpdir(), "animus-edit-test-"));
  });

  afterEach(() => {
    rmSync(tempDir, { recursive: true, force: true });
  });

  test("replaces old_string with new_string", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "hello world", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "hello",
      new_string: "goodbye",
    });

    expect(result.status).toBe("success");
    expect(readFileSync(filePath, "utf-8")).toBe("goodbye world");
  });

  test("file not found returns error", () => {
    const result = executeEdit({
      file_path: join(tempDir, "nonexistent.txt"),
      old_string: "a",
      new_string: "b",
    });
    expect(result.status).toBe("error");
    expect(result.result).toContain("File not found");
  });

  test("old_string not found returns error", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "hello world", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "missing",
      new_string: "replacement",
    });
    expect(result.status).toBe("error");
    expect(result.result).toContain("old_string not found");
  });

  test("only replaces first occurrence", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "aaa", "utf-8");

    executeEdit({
      file_path: filePath,
      old_string: "a",
      new_string: "b",
    });

    // String.replace with a string only replaces first match
    expect(readFileSync(filePath, "utf-8")).toBe("baa");
  });

  test("multi-line replacement", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "line1\nline2\nline3", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "line1\nline2",
      new_string: "replaced1\nreplaced2",
    });

    expect(result.status).toBe("success");
    expect(readFileSync(filePath, "utf-8")).toBe("replaced1\nreplaced2\nline3");
  });
});
