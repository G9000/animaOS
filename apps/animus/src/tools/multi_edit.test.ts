import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { executeMultiEdit } from "./multi_edit";

describe("multi_edit", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "multi-edit-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  test("applies multiple edits atomically", () => {
    const fp = join(dir, "test.ts");
    writeFileSync(fp, "const a = 1;\nconst b = 2;\nconst c = 3;\n");

    const result = executeMultiEdit({
      file_path: fp,
      edits: [
        { old_string: "const a = 1;", new_string: "const a = 10;" },
        { old_string: "const b = 2;", new_string: "const b = 20;" },
        { old_string: "const c = 3;", new_string: "const c = 30;" },
      ],
    });

    expect(result.status).toBe("success");
    expect(result.result).toContain("3 edit(s)");
    expect(readFileSync(fp, "utf-8")).toBe("const a = 10;\nconst b = 20;\nconst c = 30;\n");
  });

  test("rejects if any old_string is missing (no partial application)", () => {
    const fp = join(dir, "test.ts");
    writeFileSync(fp, "const a = 1;\nconst b = 2;\n");

    const result = executeMultiEdit({
      file_path: fp,
      edits: [
        { old_string: "const a = 1;", new_string: "const a = 10;" },
        { old_string: "DOES NOT EXIST", new_string: "nope" },
      ],
    });

    expect(result.status).toBe("error");
    expect(result.result).toContain("Edit 2/2");
    // File should be untouched
    expect(readFileSync(fp, "utf-8")).toBe("const a = 1;\nconst b = 2;\n");
  });

  test("file not found", () => {
    const result = executeMultiEdit({
      file_path: join(dir, "nope.ts"),
      edits: [{ old_string: "a", new_string: "b" }],
    });
    expect(result.status).toBe("error");
    expect(result.result).toContain("File not found");
  });

  test("empty edits array", () => {
    const fp = join(dir, "test.ts");
    writeFileSync(fp, "hello");
    const result = executeMultiEdit({ file_path: fp, edits: [] });
    expect(result.status).toBe("error");
    expect(result.result).toContain("No edits");
  });

  test("sequential edits see previous results", () => {
    const fp = join(dir, "test.ts");
    writeFileSync(fp, "foo bar baz");

    const result = executeMultiEdit({
      file_path: fp,
      edits: [
        { old_string: "foo", new_string: "FOO" },
        { old_string: "FOO bar", new_string: "REPLACED" },
      ],
    });

    expect(result.status).toBe("success");
    expect(readFileSync(fp, "utf-8")).toBe("REPLACED baz");
  });
});
