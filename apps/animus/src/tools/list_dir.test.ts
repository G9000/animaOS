import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { executeListDir } from "./list_dir";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("list_dir tool", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = mkdtempSync(join(tmpdir(), "animus-listdir-test-"));
  });

  afterEach(() => {
    rmSync(tempDir, { recursive: true, force: true });
  });

  test("lists files and directories with prefixes", () => {
    writeFileSync(join(tempDir, "file.txt"), "content", "utf-8");
    mkdirSync(join(tempDir, "subdir"));

    const result = executeListDir({ path: tempDir });
    expect(result.status).toBe("success");
    expect(result.result).toContain("[file] file.txt");
    expect(result.result).toContain("[dir]  subdir");
  });

  test("directory not found returns error", () => {
    const result = executeListDir({ path: join(tempDir, "nonexistent") });
    expect(result.status).toBe("error");
    expect(result.result).toContain("Directory not found");
  });

  test("empty directory returns success", () => {
    const result = executeListDir({ path: tempDir });
    expect(result.status).toBe("success");
    expect(result.result).toBe("");
  });
});
