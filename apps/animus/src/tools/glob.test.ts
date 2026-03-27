import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { executeGlob } from "./glob";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("glob tool", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = mkdtempSync(join(tmpdir(), "animus-glob-test-"));
  });

  afterEach(() => {
    rmSync(tempDir, { recursive: true, force: true });
  });

  test("finds matching files", () => {
    writeFileSync(join(tempDir, "foo.ts"), "", "utf-8");
    writeFileSync(join(tempDir, "bar.ts"), "", "utf-8");
    writeFileSync(join(tempDir, "baz.js"), "", "utf-8");

    const result = executeGlob({ pattern: "*.ts", path: tempDir });
    expect(result.status).toBe("success");
    expect(result.result).toContain("foo.ts");
    expect(result.result).toContain("bar.ts");
    expect(result.result).not.toContain("baz.js");
  });

  test("no matches returns message", () => {
    const result = executeGlob({ pattern: "*.xyz", path: tempDir });
    expect(result.status).toBe("success");
    expect(result.result).toBe("No files found");
  });

  test("nested matching with **", () => {
    mkdirSync(join(tempDir, "sub"), { recursive: true });
    writeFileSync(join(tempDir, "top.ts"), "", "utf-8");
    writeFileSync(join(tempDir, "sub", "nested.ts"), "", "utf-8");

    const result = executeGlob({ pattern: "**/*.ts", path: tempDir });
    expect(result.status).toBe("success");
    expect(result.result).toContain("top.ts");
    expect(result.result).toContain("nested.ts");
  });
});
