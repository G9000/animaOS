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

  test("rejects ambiguous match (multiple occurrences)", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "aaa", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "a",
      new_string: "b",
    });

    expect(result.status).toBe("error");
    expect(result.result).toContain("matches 3 locations");
    // File should be unchanged
    expect(readFileSync(filePath, "utf-8")).toBe("aaa");
  });

  test("handles dollar-sign patterns in new_string literally", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "const x = foo;", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "const x = foo;",
      new_string: "const x = $& bar;",
    });

    expect(result.status).toBe("success");
    // Should be literal $& not the matched text
    expect(readFileSync(filePath, "utf-8")).toBe("const x = $& bar;");
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

  test("auto-fixes over-escaped newlines from LLM", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "line1\nline2\nline3", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "line1\\nline2",  // LLM sent \\n instead of \n
      new_string: "replaced",
    });

    expect(result.status).toBe("success");
    expect(readFileSync(filePath, "utf-8")).toBe("replaced\nline3");
  });

  test("normalizes \\r\\n line endings", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "line1\r\nline2\r\nline3", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "line1\nline2",  // Agent sends \n
      new_string: "replaced",
    });

    expect(result.status).toBe("success");
  });

  test("returns smart quote hint when applicable", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "it\u2019s a smart quote", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "it's a smart quote",  // straight quote
      new_string: "replaced",
    });

    expect(result.status).toBe("error");
    expect(result.result).toContain("smart");
  });

  test("returns whitespace hint when applicable", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "  function  foo()  {\n    return 1;\n  }", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "function foo() {\n  return 1;\n}",
      new_string: "replaced",
    });

    expect(result.status).toBe("error");
    expect(result.result).toContain("whitespace");
  });
});
